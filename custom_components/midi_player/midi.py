from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

MAX_NOTE_EVENTS = 1_000_000

SMPTE_FRAME_RATES = {
    24: Fraction(24),
    25: Fraction(25),
    29: Fraction(30000, 1001),
    30: Fraction(30),
}


class MidiError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class NoteEvent:
    tick: int
    channel: int
    note: int
    is_on: bool


@dataclass(frozen=True, slots=True)
class TempoEvent:
    tick: int
    microseconds_per_quarter: int


@dataclass(frozen=True, slots=True)
class MidiFile:
    format: int
    ticks_per_beat: int | None
    ticks_per_second: Fraction | None
    notes: tuple[NoteEvent, ...]
    tempos: tuple[TempoEvent, ...]
    end_tick: int


def _read_variable_length(data: bytes, position: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if position >= len(data):
            raise MidiError("Unexpected end of data in a variable-length value")
        byte = data[position]
        position += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, position
    raise MidiError("A variable-length value is longer than four bytes")


def _parse_division(division: int) -> tuple[int | None, Fraction | None]:
    if not division & 0x8000:
        if division == 0:
            raise MidiError("The ticks per beat value must not be zero")
        return division, None
    frames = 256 - ((division >> 8) & 0xFF)
    ticks_per_frame = division & 0xFF
    if frames not in SMPTE_FRAME_RATES:
        raise MidiError(f"Unsupported SMPTE frame rate {frames}")
    if ticks_per_frame == 0:
        raise MidiError("The SMPTE ticks per frame value must not be zero")
    return None, SMPTE_FRAME_RATES[frames] * ticks_per_frame


def _parse_track(
    track: bytes, notes: list[NoteEvent], tempos: list[TempoEvent]
) -> int:
    position = 0
    tick = 0
    running_status: int | None = None
    length = len(track)
    while position < length:
        delta, position = _read_variable_length(track, position)
        tick += delta
        if position >= length:
            break
        status = track[position]
        if status < 0x80:
            if running_status is None:
                raise MidiError("A data byte appeared without a running status")
            status = running_status
        else:
            position += 1
            if status < 0xF0:
                running_status = status
        if status == 0xFF:
            if position >= length:
                raise MidiError("A meta event is truncated")
            meta_type = track[position]
            position += 1
            size, position = _read_variable_length(track, position)
            if position + size > length:
                raise MidiError("A meta event extends past the end of its track")
            payload = track[position : position + size]
            position += size
            running_status = None
            if meta_type == 0x51:
                if size != 3:
                    raise MidiError("A tempo event must contain three bytes")
                value = int.from_bytes(payload, "big")
                if value == 0:
                    raise MidiError("A tempo event has a zero tempo")
                tempos.append(TempoEvent(tick, value))
            elif meta_type == 0x2F:
                break
        elif status in (0xF0, 0xF7):
            size, position = _read_variable_length(track, position)
            if position + size > length:
                raise MidiError("A system exclusive event extends past its track")
            position += size
            running_status = None
        elif status > 0xF0:
            raise MidiError(f"Unsupported status byte 0x{status:02X} in a track")
        else:
            kind = status & 0xF0
            channel = status & 0x0F
            size = 1 if kind in (0xC0, 0xD0) else 2
            if position + size > length:
                raise MidiError("A channel event is truncated")
            first = track[position]
            second = track[position + 1] if size == 2 else 0
            if first >= 0x80 or second >= 0x80:
                raise MidiError("A channel event contains an invalid data byte")
            position += size
            if kind == 0x90:
                notes.append(NoteEvent(tick, channel, first, second > 0))
            elif kind == 0x80:
                notes.append(NoteEvent(tick, channel, first, False))
            if len(notes) > MAX_NOTE_EVENTS:
                raise MidiError("The file contains too many note events")
    return tick


def parse_midi(data: bytes) -> MidiFile:
    if len(data) < 14:
        raise MidiError("The file is too small to be a MIDI file")
    if data[:4] != b"MThd":
        raise MidiError("The file does not start with a MIDI header")
    header_length = int.from_bytes(data[4:8], "big")
    if header_length < 6 or 8 + header_length > len(data):
        raise MidiError("The MIDI header is invalid")
    midi_format = int.from_bytes(data[8:10], "big")
    division = int.from_bytes(data[12:14], "big")
    if midi_format not in (0, 1):
        raise MidiError(f"MIDI format {midi_format} is not supported")
    ticks_per_beat, ticks_per_second = _parse_division(division)

    notes: list[NoteEvent] = []
    tempos: list[TempoEvent] = []
    end_tick = 0
    track_count = 0
    position = 8 + header_length
    while position + 8 <= len(data):
        chunk_id = data[position : position + 4]
        size = int.from_bytes(data[position + 4 : position + 8], "big")
        start = position + 8
        end = min(start + size, len(data))
        position = start + size
        if chunk_id != b"MTrk":
            continue
        track_count += 1
        end_tick = max(end_tick, _parse_track(data[start:end], notes, tempos))
    if track_count == 0:
        raise MidiError("The file does not contain any tracks")

    notes.sort(key=lambda event: event.tick)
    tempos.sort(key=lambda event: event.tick)
    return MidiFile(
        format=midi_format,
        ticks_per_beat=ticks_per_beat,
        ticks_per_second=ticks_per_second,
        notes=tuple(notes),
        tempos=tuple(tempos),
        end_tick=end_tick,
    )
