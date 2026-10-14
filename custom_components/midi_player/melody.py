from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from .midi import MidiError, MidiFile

PERCUSSION_CHANNEL = 9
DEFAULT_TEMPO = 500_000
MIN_FREQUENCY = 100.0
MAX_FREQUENCY = 8000.0
MIN_SEGMENT_MS = 10
ARTICULATION_US = 20_000
MAX_SEGMENTS = 100_000


@dataclass(frozen=True, slots=True)
class Segment:
    frequency: float
    duration_ms: int


@dataclass(frozen=True, slots=True)
class Melody:
    segments: tuple[Segment, ...]
    duration_ms: int


def note_to_frequency(note: int) -> float:
    return 440.0 * 2.0 ** ((note - 69) / 12.0)


def fit_frequency(frequency: float, minimum: float, maximum: float) -> float:
    while frequency < minimum:
        frequency *= 2.0
    while frequency > maximum:
        frequency /= 2.0
    return frequency


class _TimeConverter:
    def __init__(self, midi: MidiFile) -> None:
        self._ticks_per_beat = midi.ticks_per_beat
        self._ticks_per_second = midi.ticks_per_second
        self._ticks = [0]
        self._tempos = [DEFAULT_TEMPO]
        self._bases = [0]
        if self._ticks_per_beat is None:
            return
        for event in midi.tempos:
            if event.tick == self._ticks[-1]:
                self._tempos[-1] = event.microseconds_per_quarter
                continue
            base = self.to_microseconds(event.tick)
            self._ticks.append(event.tick)
            self._tempos.append(event.microseconds_per_quarter)
            self._bases.append(base)

    def to_microseconds(self, tick: int) -> int:
        if self._ticks_per_second is not None:
            return int(tick * 1_000_000 / self._ticks_per_second)
        index = bisect_right(self._ticks, tick) - 1
        elapsed = (tick - self._ticks[index]) * self._tempos[index]
        return self._bases[index] + elapsed // self._ticks_per_beat


def _reduce_to_monophonic(
    midi_events: list, converter: _TimeConverter, end_us: int
) -> list[tuple[int, int, int | None]]:
    segments: list[tuple[int, int, int | None]] = []
    active: set[tuple[int, int]] = set()
    current: int | None = None
    start_us = 0
    index = 0
    total = len(midi_events)
    while index < total:
        tick = midi_events[index].tick
        struck: set[int] = set()
        released: set[int] = set()
        while index < total and midi_events[index].tick == tick:
            event = midi_events[index]
            key = (event.channel, event.note)
            if event.is_on:
                active.add(key)
                struck.add(event.note)
            elif key in active:
                active.discard(key)
                released.add(event.note)
            index += 1
        time_us = converter.to_microseconds(tick)
        top = max((note for _, note in active), default=None)
        if top != current:
            if time_us > start_us:
                segments.append((start_us, time_us, current))
            start_us = time_us
            current = top
        elif current is not None and current in struck and current in released:
            gap = min(ARTICULATION_US, (time_us - start_us) // 3)
            if gap >= MIN_SEGMENT_MS * 1000:
                segments.append((start_us, time_us - gap, current))
                segments.append((time_us - gap, time_us, None))
            elif time_us > start_us:
                segments.append((start_us, time_us, current))
            start_us = time_us
    if current is not None and end_us > start_us:
        segments.append((start_us, end_us, current))
    return segments


def build_melody(
    midi: MidiFile,
    *,
    include_percussion: bool = False,
    minimum_frequency: float = MIN_FREQUENCY,
    maximum_frequency: float = MAX_FREQUENCY,
) -> Melody:
    events = [
        event
        for event in midi.notes
        if include_percussion or event.channel != PERCUSSION_CHANNEL
    ]
    if not any(event.is_on for event in events):
        raise MidiError("The file contains no playable notes")

    converter = _TimeConverter(midi)
    end_us = converter.to_microseconds(max(midi.end_tick, events[-1].tick))
    raw_segments = _reduce_to_monophonic(events, converter, end_us)

    merged: list[list[float | int]] = []
    for start_us, stop_us, note in raw_segments:
        duration = (stop_us + 500) // 1000 - (start_us + 500) // 1000
        if duration <= 0:
            continue
        frequency = (
            0.0
            if note is None
            else round(
                fit_frequency(
                    note_to_frequency(note), minimum_frequency, maximum_frequency
                ),
                2,
            )
        )
        if merged and (merged[-1][0] == frequency or duration < MIN_SEGMENT_MS):
            merged[-1][1] += duration
        elif duration >= MIN_SEGMENT_MS:
            merged.append([frequency, duration])

    while merged and merged[-1][0] == 0.0:
        merged.pop()
    if not merged:
        raise MidiError("The file contains no playable notes")
    if len(merged) > MAX_SEGMENTS:
        raise MidiError("The file is too complex to play")

    segments = tuple(Segment(float(item[0]), int(item[1])) for item in merged)
    return Melody(segments, sum(segment.duration_ms for segment in segments))
