from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re

from .const import MAX_FILE_SIZE, MIDI_EXTENSIONS
from .melody import Melody, build_melody
from .midi import MidiError, parse_midi

_LOGGER = logging.getLogger(__name__)

MAX_FILENAME_LENGTH = 100
_INVALID_CHARACTERS = re.compile(r"[^\w .()\-]")


class LibraryError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class LibraryEntry:
    filename: str
    size: int
    duration_ms: int


def sanitize_filename(filename: str) -> str:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = _INVALID_CHARACTERS.sub("_", name).lstrip(".")
    lowered = name.lower()
    extension_length = next(
        (len(ext) for ext in MIDI_EXTENSIONS if lowered.endswith(ext)), None
    )
    if extension_length is None:
        raise LibraryError("Only .mid and .midi files are supported")
    stem = name[:-extension_length].strip()
    if not stem:
        raise LibraryError("The file name is empty")
    result = stem + name[-extension_length:]
    if len(result) > MAX_FILENAME_LENGTH:
        raise LibraryError(
            f"The file name is longer than {MAX_FILENAME_LENGTH} characters"
        )
    return result


class MidiLibrary:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def directory(self) -> Path:
        return self._directory

    def ensure_directory(self) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)

    def list_entries(self) -> dict[str, LibraryEntry]:
        entries: dict[str, LibraryEntry] = {}
        if not self._directory.is_dir():
            return entries
        for path in sorted(self._directory.iterdir()):
            if not path.is_file() or path.suffix.lower() not in MIDI_EXTENSIONS:
                continue
            try:
                if sanitize_filename(path.name) != path.name:
                    raise LibraryError("The file name contains unsupported characters")
                data = self._read(path)
                melody = build_melody(parse_midi(data))
            except (LibraryError, MidiError, OSError) as err:
                _LOGGER.warning("Skipping %s: %s", path.name, err)
                continue
            entries[path.name] = LibraryEntry(path.name, len(data), melody.duration_ms)
        return entries

    def save(self, filename: str, data: bytes) -> LibraryEntry:
        name = sanitize_filename(filename)
        if not data:
            raise LibraryError("The file is empty")
        if len(data) > MAX_FILE_SIZE:
            raise LibraryError(
                f"The file is larger than {MAX_FILE_SIZE // 1024} KiB"
            )
        try:
            melody = build_melody(parse_midi(data))
        except MidiError as err:
            raise LibraryError(str(err)) from err
        self.ensure_directory()
        target = self._directory / name
        temporary = self._directory / f"{name}.tmp"
        try:
            temporary.write_bytes(data)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return LibraryEntry(name, len(data), melody.duration_ms)

    def load(self, filename: str) -> Melody:
        path = self._path(filename)
        try:
            data = self._read(path)
        except FileNotFoundError as err:
            raise LibraryError(f"The file {filename} does not exist") from err
        try:
            return build_melody(parse_midi(data))
        except MidiError as err:
            raise LibraryError(str(err)) from err

    def delete(self, filename: str) -> None:
        path = self._path(filename)
        try:
            path.unlink()
        except FileNotFoundError as err:
            raise LibraryError(f"The file {filename} does not exist") from err

    def _path(self, filename: str) -> Path:
        if sanitize_filename(filename) != filename:
            raise LibraryError("The file name is invalid")
        return self._directory / filename

    def _read(self, path: Path) -> bytes:
        if path.stat().st_size > MAX_FILE_SIZE:
            raise LibraryError(
                f"The file is larger than {MAX_FILE_SIZE // 1024} KiB"
            )
        return path.read_bytes()
