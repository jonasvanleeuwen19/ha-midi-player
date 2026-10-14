import asyncio
import unittest

import support

from custom_components.midi_player.engine import (
    PlaybackEngine,
    PlaybackError,
    PlaybackState,
)
from custom_components.midi_player.melody import Melody, Segment


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self._waiters = []

    def time(self):
        return self.now

    async def sleep(self, delay):
        future = asyncio.get_running_loop().create_future()
        self._waiters.append((self.now + delay, future))
        self._waiters.sort(key=lambda waiter: waiter[0])
        await future

    async def settle(self):
        for _ in range(30):
            await asyncio.sleep(0)

    async def advance(self, seconds):
        target = self.now + seconds
        await self.settle()
        while self._waiters and self._waiters[0][0] <= target:
            wake_time, future = self._waiters.pop(0)
            self.now = max(self.now, wake_time)
            if not future.done():
                future.set_result(None)
            await self.settle()
        self.now = target
        await self.settle()


class FakeDevice:
    def __init__(self, clock):
        self.clock = clock
        self.chunks = []
        self.stops = 0
        self.fail_chunk_numbers = set()
        self.fail_stop = False

    async def send_chunk(self, buzzer, clear_queue, frequencies, durations):
        number = len(self.chunks) + 1
        self.chunks.append(
            (self.clock.now, buzzer, clear_queue, list(frequencies), list(durations))
        )
        if number in self.fail_chunk_numbers:
            raise RuntimeError("device unreachable")

    async def send_stop(self):
        self.stops += 1
        if self.fail_stop:
            raise RuntimeError("device unreachable")


def make_melody(count, duration_ms, base=440.0):
    return Melody(
        tuple(Segment(base + index, duration_ms) for index in range(count)),
        count * duration_ms,
    )


class EngineTestCase(unittest.IsolatedAsyncioTestCase):
    def build(self, **kwargs):
        self.clock = FakeClock()
        self.device = FakeDevice(self.clock)
        self.changes = 0

        def on_change():
            self.changes += 1

        self.engine = PlaybackEngine(
            self.device.send_chunk,
            self.device.send_stop,
            on_change=on_change,
            clock=self.clock.time,
            sleep=self.clock.sleep,
            **kwargs,
        )
        return self.engine

    async def asyncTearDown(self):
        if hasattr(self, "engine"):
            await self.engine.stop()


class PlaybackControlTests(EngineTestCase):
    async def test_play_sends_everything_with_clear_flag(self):
        engine = self.build()
        await engine.load(make_melody(5, 1000))
        await engine.play()
        self.assertEqual(engine.state, PlaybackState.PLAYING)
        self.assertEqual(len(self.device.chunks), 1)
        _, buzzer, clear, frequencies, durations = self.device.chunks[0]
        self.assertEqual(buzzer, 1)
        self.assertTrue(clear)
        self.assertEqual(frequencies, [440.0, 441.0, 442.0, 443.0, 444.0])
        self.assertEqual(durations, [1000] * 5)

    async def test_position_advances_with_the_clock(self):
        engine = self.build()
        await engine.load(make_melody(5, 1000))
        await engine.play()
        await self.clock.advance(2.5)
        self.assertEqual(engine.position_ms, 2500)
        self.assertEqual(engine.duration_ms, 5000)

    async def test_playback_finishes_and_silences_the_device(self):
        engine = self.build()
        await engine.load(make_melody(5, 1000))
        await engine.play()
        stops_before = self.device.stops
        await self.clock.advance(4.9)
        self.assertEqual(engine.state, PlaybackState.PLAYING)
        await self.clock.advance(0.2)
        self.assertEqual(engine.state, PlaybackState.IDLE)
        self.assertEqual(engine.position_ms, 0)
        self.assertEqual(self.device.stops, stops_before + 1)

    async def test_pause_stops_the_device_and_keeps_position(self):
        engine = self.build()
        await engine.load(make_melody(5, 1000))
        await engine.play()
        await self.clock.advance(2.5)
        stops_before = self.device.stops
        await engine.pause()
        self.assertEqual(engine.state, PlaybackState.PAUSED)
        self.assertEqual(engine.position_ms, 2500)
        self.assertEqual(self.device.stops, stops_before + 1)
        await self.clock.advance(10)
        self.assertEqual(engine.position_ms, 2500)
        self.assertEqual(engine.state, PlaybackState.PAUSED)

    async def test_resume_continues_from_the_middle_of_a_segment(self):
        engine = self.build()
        await engine.load(make_melody(5, 1000))
        await engine.play()
        await self.clock.advance(2.5)
        await engine.pause()
        await engine.resume()
        self.assertEqual(engine.state, PlaybackState.PLAYING)
        _, _, clear, frequencies, durations = self.device.chunks[-1]
        self.assertTrue(clear)
        self.assertEqual(frequencies, [442.0, 443.0, 444.0])
        self.assertEqual(durations, [500, 1000, 1000])
        await self.clock.advance(2.4)
        self.assertEqual(engine.state, PlaybackState.PLAYING)
        await self.clock.advance(0.2)
        self.assertEqual(engine.state, PlaybackState.IDLE)

    async def test_stop_resets_state_and_silences(self):
        engine = self.build()
        await engine.load(make_melody(5, 1000))
        await engine.play()
        await self.clock.advance(1)
        stops_before = self.device.stops
        await engine.stop()
        self.assertEqual(engine.state, PlaybackState.IDLE)
        self.assertEqual(engine.position_ms, 0)
        self.assertEqual(self.device.stops, stops_before + 1)
        chunks_before = len(self.device.chunks)
        await self.clock.advance(20)
        self.assertEqual(len(self.device.chunks), chunks_before)

    async def test_play_again_restarts_from_the_beginning(self):
        engine = self.build()
        await engine.load(make_melody(5, 1000))
        await engine.play()
        await self.clock.advance(3)
        await engine.play()
        self.assertEqual(engine.position_ms, 0)
        _, _, clear, frequencies, _ = self.device.chunks[-1]
        self.assertTrue(clear)
        self.assertEqual(len(frequencies), 5)

    async def test_pause_and_resume_when_idle_do_nothing(self):
        engine = self.build()
        await engine.load(make_melody(2, 1000))
        await engine.pause()
        await engine.resume()
        self.assertEqual(engine.state, PlaybackState.IDLE)
        self.assertEqual(self.device.chunks, [])
        self.assertEqual(self.device.stops, 0)

    async def test_resume_when_playing_does_nothing(self):
        engine = self.build()
        await engine.load(make_melody(3, 1000))
        await engine.play()
        await engine.resume()
        self.assertEqual(len(self.device.chunks), 1)

    async def test_stop_twice_is_safe(self):
        engine = self.build()
        await engine.load(make_melody(3, 1000))
        await engine.play()
        await engine.stop()
        await engine.stop()
        self.assertEqual(engine.state, PlaybackState.IDLE)

    async def test_play_without_melody_raises(self):
        engine = self.build()
        with self.assertRaises(PlaybackError):
            await engine.play()

    async def test_loading_a_new_melody_stops_playback(self):
        engine = self.build()
        await engine.load(make_melody(5, 1000))
        await engine.play()
        stops_before = self.device.stops
        await engine.load(make_melody(2, 500))
        self.assertEqual(engine.state, PlaybackState.IDLE)
        self.assertEqual(engine.duration_ms, 1000)
        self.assertEqual(self.device.stops, stops_before + 1)

    async def test_unloading_clears_the_melody(self):
        engine = self.build()
        await engine.load(make_melody(5, 1000))
        await engine.load(None)
        self.assertIsNone(engine.melody)
        self.assertEqual(engine.duration_ms, 0)
        with self.assertRaises(PlaybackError):
            await engine.play()

    async def test_state_changes_are_reported(self):
        engine = self.build()
        await engine.load(make_melody(3, 1000))
        before = self.changes
        await engine.play()
        await engine.pause()
        await engine.resume()
        await engine.stop()
        self.assertGreaterEqual(self.changes - before, 4)


class SeekAndBuzzerTests(EngineTestCase):
    async def test_seek_while_playing_restarts_at_the_target(self):
        engine = self.build()
        await engine.load(make_melody(5, 1000))
        await engine.play()
        await self.clock.advance(1)
        await engine.seek(3200)
        self.assertEqual(engine.state, PlaybackState.PLAYING)
        self.assertEqual(engine.position_ms, 3200)
        _, _, clear, frequencies, durations = self.device.chunks[-1]
        self.assertTrue(clear)
        self.assertEqual(frequencies, [443.0, 444.0])
        self.assertEqual(durations, [800, 1000])

    async def test_seek_while_paused_only_moves_the_position(self):
        engine = self.build()
        await engine.load(make_melody(5, 1000))
        await engine.play()
        await engine.pause()
        chunks_before = len(self.device.chunks)
        await engine.seek(4000)
        self.assertEqual(engine.state, PlaybackState.PAUSED)
        self.assertEqual(engine.position_ms, 4000)
        self.assertEqual(len(self.device.chunks), chunks_before)
        await engine.resume()
        self.assertEqual(self.device.chunks[-1][4], [1000])

    async def test_seek_is_clamped(self):
        engine = self.build()
        await engine.load(make_melody(5, 1000))
        await engine.play()
        await engine.seek(999999)
        self.assertEqual(engine.position_ms, 4999)
        await engine.seek(-5)
        self.assertEqual(engine.position_ms, 0)

    async def test_seek_when_idle_raises(self):
        engine = self.build()
        await engine.load(make_melody(5, 1000))
        with self.assertRaises(PlaybackError):
            await engine.seek(100)

    async def test_buzzer_change_while_playing_keeps_position(self):
        engine = self.build()
        await engine.load(make_melody(5, 1000))
        await engine.play()
        await self.clock.advance(2)
        await engine.set_buzzer(2)
        self.assertEqual(engine.buzzer, 2)
        self.assertEqual(engine.state, PlaybackState.PLAYING)
        _, buzzer, clear, _, durations = self.device.chunks[-1]
        self.assertEqual(buzzer, 2)
        self.assertTrue(clear)
        self.assertEqual(durations, [1000, 1000, 1000])
        self.assertEqual(engine.position_ms, 2000)

    async def test_buzzer_change_while_idle_is_used_on_next_play(self):
        engine = self.build()
        await engine.load(make_melody(2, 1000))
        await engine.set_buzzer(2)
        self.assertEqual(self.device.chunks, [])
        await engine.play()
        self.assertEqual(self.device.chunks[0][1], 2)

    async def test_invalid_buzzer_is_rejected(self):
        engine = self.build(buzzer_count=2)
        for buzzer in (0, 3, -1):
            with self.assertRaises(PlaybackError):
                await engine.set_buzzer(buzzer)

    async def test_every_configured_buzzer_can_be_selected(self):
        engine = self.build(buzzer_count=4)
        await engine.load(make_melody(2, 1000))
        for buzzer in (1, 2, 3, 4):
            await engine.set_buzzer(buzzer)
            await engine.play()
            self.assertEqual(self.device.chunks[-1][1], buzzer)
        self.assertEqual(engine.buzzer_count, 4)


class StreamingTests(EngineTestCase):
    async def test_long_melody_is_streamed_in_chunks_with_lookahead(self):
        engine = self.build(chunk_size=50, lookahead_ms=5000)
        await engine.load(make_melody(300, 100))
        await engine.play()
        await self.clock.advance(31)
        times = [chunk[0] for chunk in self.device.chunks]
        self.assertEqual(times, [0, 0, 7.5, 12.5, 17.5, 22.5])
        self.assertEqual([chunk[2] for chunk in self.device.chunks], [True] + [False] * 5)
        sent = [duration for chunk in self.device.chunks for duration in chunk[4]]
        self.assertEqual(sum(sent), 30000)
        self.assertEqual(len(sent), 300)
        self.assertTrue(all(len(chunk[3]) <= 50 for chunk in self.device.chunks))
        self.assertEqual(engine.state, PlaybackState.IDLE)

    async def test_stop_during_streaming_cancels_remaining_chunks(self):
        engine = self.build(chunk_size=50, lookahead_ms=5000)
        await engine.load(make_melody(300, 100))
        await engine.play()
        await self.clock.advance(3)
        await engine.stop()
        count = len(self.device.chunks)
        await self.clock.advance(60)
        self.assertEqual(len(self.device.chunks), count)

    async def test_pause_during_streaming_then_resume_sends_remaining_audio_once(self):
        engine = self.build(chunk_size=50, lookahead_ms=5000)
        await engine.load(make_melody(300, 100))
        await engine.play()
        await self.clock.advance(8)
        await engine.pause()
        self.assertEqual(engine.position_ms, 8000)
        await engine.resume()
        await self.clock.advance(23)
        self.assertEqual(engine.state, PlaybackState.IDLE)
        resumed_audio = sum(
            sum(chunk[4])
            for chunk in self.device.chunks[self._index_of_last_clear() :]
        )
        self.assertEqual(resumed_audio, 22000)

    def _index_of_last_clear(self):
        return max(i for i, chunk in enumerate(self.device.chunks) if chunk[2])


class ErrorHandlingTests(EngineTestCase):
    async def test_failure_on_start_propagates_and_stays_idle(self):
        engine = self.build()
        await engine.load(make_melody(3, 1000))
        self.device.fail_chunk_numbers = {1}
        with self.assertRaises(RuntimeError):
            await engine.play()
        self.assertEqual(engine.state, PlaybackState.IDLE)
        self.assertEqual(engine.position_ms, 0)

    async def test_failure_while_streaming_stops_playback_and_records_error(self):
        engine = self.build(chunk_size=50, lookahead_ms=5000)
        await engine.load(make_melody(300, 100))
        self.device.fail_chunk_numbers = {3}
        await engine.play()
        stops_before = self.device.stops
        await self.clock.advance(10)
        self.assertEqual(engine.state, PlaybackState.IDLE)
        self.assertIn("device unreachable", engine.last_error)
        self.assertEqual(self.device.stops, stops_before + 1)

    async def test_stop_failure_after_finish_is_swallowed(self):
        engine = self.build()
        await engine.load(make_melody(2, 1000))
        await engine.play()
        self.device.fail_stop = True
        await self.clock.advance(3)
        self.assertEqual(engine.state, PlaybackState.IDLE)
        self.assertIsNone(engine.last_error)
        self.device.fail_stop = False

    async def test_stop_failure_on_explicit_stop_propagates_after_state_reset(self):
        engine = self.build()
        await engine.load(make_melody(2, 1000))
        await engine.play()
        self.device.fail_stop = True
        with self.assertRaises(RuntimeError):
            await engine.stop()
        self.assertEqual(engine.state, PlaybackState.IDLE)
        self.device.fail_stop = False

    async def test_error_is_cleared_by_next_successful_start(self):
        engine = self.build(chunk_size=50, lookahead_ms=5000)
        await engine.load(make_melody(300, 100))
        self.device.fail_chunk_numbers = {3}
        await engine.play()
        await self.clock.advance(10)
        self.assertIsNotNone(engine.last_error)
        await engine.play()
        self.assertIsNone(engine.last_error)


class CapacityTests(unittest.TestCase):
    def test_streaming_limits_fit_the_device_queue(self):
        from custom_components.midi_player.engine import CHUNK_SIZE, LOOKAHEAD_MS
        from custom_components.midi_player.melody import MIN_SEGMENT_MS

        worst_case = LOOKAHEAD_MS // MIN_SEGMENT_MS + CHUNK_SIZE
        self.assertLess(worst_case, 2048)


if __name__ == "__main__":
    unittest.main()
