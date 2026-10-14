from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parent.parent
COMPONENT_DIR = ROOT / "custom_components" / "midi_player"
HOMEASSISTANT_AVAILABLE = importlib.util.find_spec("homeassistant") is not None


def _register_bare_package() -> None:
    if "custom_components.midi_player" in sys.modules:
        return
    namespace = types.ModuleType("custom_components")
    namespace.__path__ = [str(ROOT / "custom_components")]
    package = types.ModuleType("custom_components.midi_player")
    package.__path__ = [str(COMPONENT_DIR)]
    namespace.midi_player = package
    sys.modules["custom_components"] = namespace
    sys.modules["custom_components.midi_player"] = package


_register_bare_package()


def variable_length(value: int) -> bytes:
    parts = [value & 0x7F]
    value >>= 7
    while value:
        parts.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(parts))


def event(delta: int, data: bytes) -> bytes:
    return variable_length(delta) + data


def note_on(delta: int, note: int, velocity: int = 100, channel: int = 0) -> bytes:
    return event(delta, bytes([0x90 | channel, note, velocity]))


def note_off(delta: int, note: int, channel: int = 0) -> bytes:
    return event(delta, bytes([0x80 | channel, note, 0]))


def tempo(delta: int, microseconds_per_quarter: int) -> bytes:
    return event(delta, b"\xff\x51\x03" + microseconds_per_quarter.to_bytes(3, "big"))


def end_of_track(delta: int = 0) -> bytes:
    return event(delta, b"\xff\x2f\x00")


def track(*events: bytes) -> bytes:
    body = b"".join(events)
    return b"MTrk" + len(body).to_bytes(4, "big") + body


def midi_file(tracks: list[bytes], division: int = 480, midi_format: int = 1) -> bytes:
    header = (
        b"MThd"
        + (6).to_bytes(4, "big")
        + midi_format.to_bytes(2, "big")
        + len(tracks).to_bytes(2, "big")
        + division.to_bytes(2, "big")
    )
    return header + b"".join(tracks)


def single_note_file(note: int = 69, ticks: int = 480) -> bytes:
    return midi_file(
        [track(note_on(0, note), note_off(ticks, note), end_of_track())],
        midi_format=0,
    )
