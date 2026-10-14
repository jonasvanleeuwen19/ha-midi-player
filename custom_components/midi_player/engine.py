from __future__ import annotations

import asyncio
from bisect import bisect_right
from collections.abc import Awaitable, Callable, Coroutine
from enum import StrEnum
import logging
import time
from typing import Any

from .const import MAX_BUZZERS
from .melody import Melody

_LOGGER = logging.getLogger(__name__)

LOOKAHEAD_MS = 10_000
CHUNK_SIZE = 100


class PlaybackError(Exception):
    pass


class PlaybackState(StrEnum):
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"


class RepeatMode(StrEnum):
    OFF = "off"
    ONE = "one"
    ALL = "all"


SendChunk = Callable[[int, bool, list[float], list[int]], Awaitable[None]]
SendStop = Callable[[], Awaitable[None]]
TaskFactory = Callable[[Coroutine[Any, Any, None]], "asyncio.Task[None]"]


class PlaybackEngine:
    def __init__(
        self,
        send_chunk: SendChunk,
        send_stop: SendStop,
        *,
        on_change: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        create_task: TaskFactory | None = None,
        lookahead_ms: int = LOOKAHEAD_MS,
        chunk_size: int = CHUNK_SIZE,
        buzzer_count: int = MAX_BUZZERS,
    ) -> None:
        self._send_chunk = send_chunk
        self._send_stop = send_stop
        self._on_change = on_change
        self._clock = clock
        self._sleep = sleep
        self._create_task = create_task or asyncio.create_task
        self._lookahead_ms = lookahead_ms
        self._chunk_size = chunk_size
        self._lock = asyncio.Lock()
        self._melody: Melody | None = None
        self._starts: list[int] = []
        self._state = PlaybackState.IDLE
        self._repeat = RepeatMode.OFF
        self._buzzer_count = buzzer_count
        self._buzzer = 1
        self._position_ms = 0
        self._origin_ms = 0
        self._started_at = 0.0
        self._task: asyncio.Task[None] | None = None
        self._last_error: str | None = None

    @property
    def state(self) -> PlaybackState:
        return self._state

    @property
    def repeat(self) -> RepeatMode:
        return self._repeat

    @property
    def buzzer(self) -> int:
        return self._buzzer

    @property
    def buzzer_count(self) -> int:
        return self._buzzer_count

    @property
    def melody(self) -> Melody | None:
        return self._melody

    @property
    def duration_ms(self) -> int:
        return self._melody.duration_ms if self._melody else 0

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def position_ms(self) -> int:
        if self._state is PlaybackState.PLAYING:
            elapsed = (self._clock() - self._started_at) * 1000
            return int(min(self.duration_ms, self._origin_ms + elapsed))
        return self._position_ms

    async def set_repeat(self, repeat: RepeatMode | str) -> None:
        try:
            mode = RepeatMode(repeat)
        except ValueError as err:
            raise PlaybackError(f"Unknown repeat mode: {repeat}") from err

        async with self._lock:
            self._repeat = mode
            self._notify()

    async def load(self, melody: Melody | None) -> None:
        async with self._lock:
            was_active = self._state is not PlaybackState.IDLE
            await self._cancel_stream()
            self._melody = melody
            self._starts = []

            if melody is not None:
                total = 0
                for segment in melody.segments:
                    self._starts.append(total)
                    total += segment.duration_ms

            self._position_ms = 0
            self._state = PlaybackState.IDLE
            self._last_error = None
            self._notify()

            if was_active:
                await self._send_stop()

    async def play(self, position_ms: int = 0) -> None:
        async with self._lock:
            self._require_melody()
            await self._cancel_stream()
            await self._begin(position_ms)

    async def pause(self) -> None:
        async with self._lock:
            if self._state is not PlaybackState.PLAYING:
                return

            position = self.position_ms
            await self._cancel_stream()

            if self._state is not PlaybackState.PLAYING:
                return

            self._position_ms = position
            self._state = PlaybackState.PAUSED
            self._notify()
            await self._send_stop()

    async def resume(self) -> None:
        async with self._lock:
            if self._state is not PlaybackState.PAUSED:
                return
            await self._begin(self._position_ms)

    async def stop(self) -> None:
        async with self._lock:
            await self._cancel_stream()
            self._position_ms = 0
            self._state = PlaybackState.IDLE
            self._notify()
            await self._send_stop()

    async def seek(self, position_ms: int) -> None:
        async with self._lock:
            if self._state is PlaybackState.IDLE:
                raise PlaybackError("Nothing is playing")

            target = max(0, min(int(position_ms), self.duration_ms - 1))

            if self._state is PlaybackState.PLAYING:
                await self._cancel_stream()
                await self._begin(target)
            else:
                self._position_ms = target
                self._notify()

    async def set_buzzer(self, buzzer: int) -> None:
        if not 1 <= buzzer <= self._buzzer_count:
            raise PlaybackError(f"Buzzer {buzzer} does not exist")

        async with self._lock:
            if buzzer == self._buzzer:
                return

            self._buzzer = buzzer

            if self._state is PlaybackState.PLAYING:
                position = self.position_ms
                await self._cancel_stream()
                await self._begin(position)
            else:
                self._notify()

    def _require_melody(self) -> Melody:
        if self._melody is None:
            raise PlaybackError("No MIDI file is selected")
        return self._melody

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change()

    async def _cancel_stream(self) -> None:
        task = self._task
        self._task = None

        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _take(
        self,
        index: int,
        offset: int,
        sent_ms: int,
    ) -> tuple[list[float], list[int], int, int]:
        segments = self._require_melody().segments
        end = min(len(segments), index + self._chunk_size)
        frequencies: list[float] = []
        durations: list[int] = []

        for position in range(index, end):
            segment = segments[position]
            duration = segment.duration_ms - (
                offset if position == index else 0
            )
            frequencies.append(segment.frequency)
            durations.append(duration)
            sent_ms += duration

        return frequencies, durations, end, sent_ms

    async def _begin(self, position_ms: int) -> None:
        melody = self._require_melody()

        if not melody.segments or melody.duration_ms <= 0:
            raise PlaybackError("The selected MIDI file is empty")

        position = max(0, min(int(position_ms), self.duration_ms - 1))
        index = bisect_right(self._starts, position) - 1
        offset = position - self._starts[index]

        frequencies, durations, next_index, sent_ms = self._take(
            index, offset, position
        )

        self._last_error = None

        try:
            await self._send_chunk(
                self._buzzer, True, frequencies, durations
            )
        except Exception:
            self._position_ms = 0
            self._state = PlaybackState.IDLE
            self._notify()
            raise

        self._origin_ms = position
        self._started_at = self._clock()
        self._position_ms = position
        self._state = PlaybackState.PLAYING
        self._notify()
        self._task = self._create_task(self._stream(next_index, sent_ms))

    async def _stream(self, next_index: int, sent_ms: int) -> None:
        try:
            total = len(self._require_melody().segments)

            while next_index < total:
                lead = sent_ms - self.position_ms

                if lead > self._lookahead_ms:
                    await self._sleep(
                        (lead - self._lookahead_ms / 2) / 1000
                    )
                    continue

                frequencies, durations, next_index, sent_ms = self._take(
                    next_index, 0, sent_ms
                )

                await self._send_chunk(
                    self._buzzer, False, frequencies, durations
                )

            remaining = self.duration_ms - self.position_ms

            if remaining > 0:
                await self._sleep(remaining / 1000)

        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.error("Playback stopped because of an error: %s", err)
            self._position_ms = 0
            self._state = PlaybackState.IDLE
            self._last_error = str(err)
            self._notify()
            await self._send_stop_quietly()
            return

        async with self._lock:
            if self._state is not PlaybackState.PLAYING:
                return

            try:
                await self._send_stop()
            except Exception as err:
                _LOGGER.warning("Could not stop playback cleanly: %s", err)

            if self._repeat is not RepeatMode.OFF:
                try:
                    await self._begin(0)
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    _LOGGER.error("Could not repeat MIDI playback: %s", err)
                    self._last_error = str(err)

            self._position_ms = 0
            self._state = PlaybackState.IDLE
            self._task = None
            self._notify()

    async def _send_stop_quietly(self) -> None:
        try:
            await self._send_stop()
        except Exception as err:
            _LOGGER.warning(
                "Could not send the stop command to the device: %s", err
            )