# MIDI Player for Home Assistant

Play MIDI files on any ESPHome device with a buzzer. Upload a `.mid` file in Home Assistant, press play and your ESP32 plays the melody.

* Works with Home Assistant Container, Core, OS and Supervised. No add-ons, Node.js servers or extra containers.
* Works with any ESP32 that has one to four passive buzzers on LEDC outputs.
* Supports several ESPHome devices at the same time.
* Timing runs on the ESP32. Home Assistant sends small batches of notes ahead of time instead of one call per note.
* Native media player entity with play, pause, resume, stop, seek, progress and duration.
* Dashboard card with file upload. It is loaded automatically, no resource has to be added by hand.
* Installable with HACS.

## How it works

1. You upload a MIDI file. Home Assistant validates it and stores it in `<config>/midi_player/`.
2. The file is parsed (tempo changes, ticks per beat or SMPTE timing, note on and off, overlapping notes) and converted into a list of `(frequency, duration)` steps.
3. The steps are streamed to the ESP32 in batches of up to 100, keeping about ten seconds buffered. The ESP32 plays them with its own clock and absolute deadlines, so timing errors do not add up.
4. Pause and stop silence the buzzer immediately. Resume continues from the exact position.

The ESP32 side is a small ESPHome package that adds two actions to the native API: `midi_play_chunk` and `midi_stop`. Home Assistant exposes them as `esphome.<device_name>_midi_play_chunk` and `esphome.<device_name>_midi_stop`, with dashes in the device name replaced by underscores. The integration uses your existing ESPHome connection, so it does not need IP addresses, keys or passwords.

## Requirements

* Home Assistant 2025.1 or newer.
* [HACS](https://hacs.xyz). On Home Assistant Container, HACS is installed manually, see the HACS documentation.
* An ESP32 running ESPHome and added to Home Assistant with the ESPHome integration.
* One to four passive buzzers, each driven by an [LEDC output](https://esphome.io/components/output/ledc/). Active buzzers only produce one fixed tone and cannot play melodies.

## Installation

### 1. Add the package to your ESPHome configuration

Add the package to your existing device configuration. Nothing else in your configuration changes.

```yaml
packages:
  midi_player: github://jonasvanleeuwen19/ha-midi-player/esphome/midi_player.yaml@main
```

Tell the package which outputs are your buzzers with substitutions. The defaults are `buzzer_1` for the first buzzer and the same output for all other slots, so a single buzzer with the id `buzzer_1` needs no substitutions at all.

```yaml
substitutions:
  midi_buzzer_1: buzzer_1
  midi_buzzer_2: buzzer_2
```

| Substitution | Default | Description |
| --- | --- | --- |
| `midi_buzzer_1` | `buzzer_1` | ID of the LEDC output for buzzer 1 |
| `midi_buzzer_2` | value of `midi_buzzer_1` | ID of the LEDC output for buzzer 2 |
| `midi_buzzer_3` | value of `midi_buzzer_1` | ID of the LEDC output for buzzer 3 |
| `midi_buzzer_4` | value of `midi_buzzer_1` | ID of the LEDC output for buzzer 4 |
| `midi_duty` | `50%` | Duty cycle while a note sounds, which also changes the volume |

A complete example is in [`esphome/example.yaml`](esphome/example.yaml). If you prefer not to use a remote package, copy [`esphome/midi_player.yaml`](esphome/midi_player.yaml) next to your configuration and use `midi_player: !include midi_player.yaml` under `packages:`.

Install the configuration on the device. Under Developer tools, Actions in Home Assistant, the action `esphome.<device_name>_midi_play_chunk` should now exist.

Existing switches that turn a buzzer output on permanently conflict with melodies. Keep them off while a melody plays.

The two LEDC channels of an ESP32 may share a timer. This does not matter here because only one buzzer sounds at a time.

### 2. Install the integration with HACS

1. Open HACS, choose the menu, then Custom repositories.
2. Enter `https://github.com/OWNER/midi_player` and choose the category Integration.
3. Download MIDI Player and restart Home Assistant. On Docker, restart the container.

### 3. Add the integration

1. Go to Settings, Devices and services, Add integration and search for MIDI Player.
2. Select your ESPHome device and enter the number of buzzers (1 to 4).

Add the integration once for every ESPHome device that should play melodies. You can change the number of buzzers later with Reconfigure.

### 4. Add the card

The card is registered automatically. Edit a dashboard, add a card and search for MIDI Player, or use YAML:

```yaml
type: custom:midi-player-card
entity: media_player.my_device_midi_player
```

Replace the entity with the media player created for your device. If the card does not appear, reload the page with Ctrl+F5 or clear the cache of the mobile app.

## Usage

* Use the file picker in the card and press Upload. Uploading requires an administrator account.
* Choose a file and a buzzer, then press Play.
* Click the progress bar to seek.
* Delete removes the selected file.
* A new upload with an existing name replaces the file. Playback of that file stops and the new version is loaded.
* Changing the buzzer while playing continues on the other buzzer at the same position.
* The selected file and buzzer are remembered after a restart. Playback always starts stopped.

The buzzer is a sound mode of the media player, so it is also available in the more-info dialog and in automations.

Files are shared between all configured devices. You can also copy files into `<config>/midi_player/` and run the action `midi_player.rescan_library`.

### Actions

| Action | Description |
| --- | --- |
| `media_player.media_play`, `media_pause`, `media_stop`, `media_seek` | Standard media player controls |
| `media_player.select_source` | Selects a MIDI file |
| `media_player.select_sound_mode` | Selects the buzzer, for example `Buzzer 2` |
| `midi_player.play_file` | Selects a file and plays it from the start. Fields: `filename`, optional `buzzer` |
| `midi_player.delete_file` | Deletes a file from the library. Administrators only |
| `midi_player.rescan_library` | Reads the library folder again. Administrators only |

Example automation:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.my_device_button
    to: "on"
actions:
  - action: midi_player.play_file
    target:
      entity_id: media_player.my_device_midi_player
    data:
      filename: song.mid
      buzzer: 1
```

## How MIDI files are played

A buzzer plays one frequency at a time, so simultaneous notes are reduced to a single melody line:

* The highest active note is played.
* The percussion channel (channel 10) is ignored.
* The same pitch on several channels sounds as one tone.
* When a note is struck again right after it ended, a short silence of up to 20 ms keeps repeated notes audible.
* Steps shorter than 10 ms are merged into the previous step.
* Frequencies outside 100 Hz to 8000 Hz are shifted by whole octaves into that range.
* Note numbers are converted with `440 * 2^((note - 69) / 12)`.

Supported: MIDI formats 0 and 1, tempo changes, ticks per beat and SMPTE timing. Format 2 files are rejected. Files up to 1 MiB with `.mid` or `.midi` extension are accepted, and malformed files or files without pitched notes are rejected with an error message.

Songs with many fast notes in several tracks can sound busy, because the highest note of all tracks is played.

## Troubleshooting

| Problem | Solution |
| --- | --- |
| The setup dialog reports that the actions are missing | The device is offline or the package is not installed yet. Install the ESPHome configuration first |
| No ESPHome devices are listed | Add the device with the ESPHome integration first |
| Playback stops with an error about an unavailable action | The device went offline. The message is shown in the `last_error` attribute of the media player |
| No sound | Check that the buzzer is passive and that the substitutions point to the right output IDs |
| The card is not found | Reload the page with Ctrl+F5 and check that the integration is set up |

## Development

```
pip install -r requirements-test.txt
python -m unittest discover -s tests -v
```

The tests do not need Home Assistant. They cover MIDI parsing, tempo changes, scheduling, playback controls with a virtual clock, error handling, the file library, the manager with fake Home Assistant objects, and consistency checks of the repository and the ESPHome package.

## Status

This project is in beta. The core logic is covered by automated tests, but the Home Assistant integration and the ESPHome package depend on your setup. Please report problems in the issue tracker and include your Home Assistant version, ESPHome version and relevant log lines.

## License

MIT, see [LICENSE](LICENSE).
