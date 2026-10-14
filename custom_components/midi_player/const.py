from __future__ import annotations

DOMAIN = "midi_player"
ESPHOME_DOMAIN = "esphome"

CONF_ESPHOME_ENTRY_ID = "esphome_entry_id"
CONF_BUZZER_COUNT = "buzzer_count"
ESPHOME_DEVICE_NAME_KEY = "device_name"

ACTION_PLAY_CHUNK = "midi_play_chunk"
ACTION_STOP = "midi_stop"

SERVICE_PLAY_FILE = "play_file"
SERVICE_DELETE_FILE = "delete_file"
SERVICE_RESCAN_LIBRARY = "rescan_library"

ATTR_FILENAME = "filename"
ATTR_BUZZER = "buzzer"

WS_UPLOAD = f"{DOMAIN}/upload"

LIBRARY_DIRECTORY = "midi_player"
STORAGE_VERSION = 1

STATIC_URL_PATH = "/midi_player_static"
CARD_FILENAME = "midi-player-card.js"
CARD_VERSION = "1.0.0"

MAX_FILE_SIZE = 1024 * 1024
MAX_ENCODED_SIZE = (MAX_FILE_SIZE + 2) // 3 * 4 + 4
MIDI_EXTENSIONS = (".mid", ".midi")

MAX_BUZZERS = 4
DEFAULT_BUZZER_COUNT = 1
