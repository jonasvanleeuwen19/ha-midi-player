const MAX_UPLOAD_BYTES = 1024 * 1024;

const STYLE = `
  .content { padding: 0 16px 16px; display: grid; gap: 12px; }
  .status { font-size: 1.05em; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .status small { display: block; font-weight: 400; color: var(--secondary-text-color); }
  .row { display: flex; align-items: center; gap: 8px; }
  .row label { min-width: 56px; color: var(--secondary-text-color); }
  select, input[type="file"] { flex: 1; min-width: 0; font: inherit; color: var(--primary-text-color); }
  select { padding: 8px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--card-background-color); }
  button { font: inherit; padding: 8px 14px; border-radius: 8px; border: none; cursor: pointer; background: var(--primary-color); color: var(--text-primary-color, #fff); }
  button.secondary { background: transparent; color: var(--primary-text-color); border: 1px solid var(--divider-color); }
  button:disabled { opacity: 0.5; cursor: default; }
  .buttons { display: flex; gap: 8px; flex-wrap: wrap; }
  .bar { height: 10px; border-radius: 5px; background: var(--divider-color); cursor: pointer; overflow: hidden; }
  .fill { height: 100%; width: 0; background: var(--primary-color); }
  .times { display: flex; justify-content: space-between; font-size: 0.85em; color: var(--secondary-text-color); }
  .message { min-height: 1.2em; font-size: 0.9em; color: var(--secondary-text-color); }
  .message.error { color: var(--error-color); }
`;

function formatTime(totalSeconds) {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function toBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  const step = 0x8000;
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += step) {
    binary += String.fromCharCode.apply(null, bytes.subarray(offset, offset + step));
  }
  return btoa(binary);
}

function create(tag, props = {}, children = []) {
  const element = document.createElement(tag);
  Object.assign(element, props);
  for (const child of children) {
    element.append(child);
  }
  return element;
}

class MidiPlayerCard extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._config = null;
    this._elements = null;
    this._timer = null;
    this._optionsKey = "";
    this._buzzerKey = "";
  }

  static getStubConfig(hass) {
    const states = (hass && hass.states) || {};
    const player = Object.keys(states).find(
      (id) => id.startsWith("media_player.") && states[id].attributes.file_count !== undefined
    );
    return { entity: player || "media_player.midi_player" };
  }

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("The entity option is required");
    }
    this._config = { title: "MIDI Player", ...config };
    this._build();
    this._refresh();
  }

  set hass(hass) {
    this._hass = hass;
    this._refresh();
  }

  getCardSize() {
    return 6;
  }

  connectedCallback() {
    if (this._timer === null) {
      this._timer = window.setInterval(() => this._updateProgress(), 500);
    }
  }

  disconnectedCallback() {
    if (this._timer !== null) {
      window.clearInterval(this._timer);
      this._timer = null;
    }
  }

  _build() {
    const root = this.shadowRoot || this.attachShadow({ mode: "open" });
    root.replaceChildren();
    this._optionsKey = "";
    this._buzzerKey = "";

    const e = {};
    e.status = create("div", { className: "status" });
    e.fileSelect = create("select");
    e.buzzerSelect = create("select");
    e.fill = create("div", { className: "fill" });
    e.bar = create("div", { className: "bar" }, [e.fill]);
    e.position = create("span", { textContent: "0:00" });
    e.duration = create("span", { textContent: "0:00" });
    e.play = create("button", { textContent: "Play" });
    e.pause = create("button", { textContent: "Pause", className: "secondary" });
    e.stop = create("button", { textContent: "Stop", className: "secondary" });
    e.upload = create("button", { textContent: "Upload", className: "secondary" });
    e.remove = create("button", { textContent: "Delete", className: "secondary" });
    e.input = create("input", { type: "file", accept: ".mid,.midi,audio/midi,audio/x-midi" });
    e.message = create("div", { className: "message" });

    e.fileSelect.addEventListener("change", () => {
      if (e.fileSelect.value) {
        this._call("media_player", "select_source", { source: e.fileSelect.value });
      }
    });
    e.buzzerSelect.addEventListener("change", () => {
      if (e.buzzerSelect.value) {
        this._call("media_player", "select_sound_mode", { sound_mode: e.buzzerSelect.value });
      }
    });
    e.play.addEventListener("click", () => this._call("media_player", "media_play"));
    e.pause.addEventListener("click", () => this._call("media_player", "media_pause"));
    e.stop.addEventListener("click", () => this._call("media_player", "media_stop"));
    e.bar.addEventListener("click", (event) => this._seek(event));
    e.upload.addEventListener("click", () => this._upload());
    e.remove.addEventListener("click", () => this._delete());

    const row = (label, control) =>
      create("div", { className: "row" }, [create("label", { textContent: label }), control]);
    e.buzzerRow = row("Buzzer", e.buzzerSelect);

    const content = create("div", { className: "content" }, [
      e.status,
      row("File", e.fileSelect),
      e.buzzerRow,
      create("div", {}, [e.bar, create("div", { className: "times" }, [e.position, e.duration])]),
      create("div", { className: "buttons" }, [e.play, e.pause, e.stop]),
      create("div", { className: "row" }, [e.input, e.upload, e.remove]),
      e.message,
    ]);

    const card = create("ha-card", {}, [content]);
    card.header = this._config.title;
    root.append(create("style", { textContent: STYLE }), card);
    this._elements = e;
  }

  _stateObject() {
    return this._hass && this._config ? this._hass.states[this._config.entity] : undefined;
  }

  _refresh() {
    const e = this._elements;
    if (!e || !this._hass) {
      return;
    }
    const stateObj = this._stateObject();
    if (!stateObj) {
      e.status.textContent = `Entity not found: ${this._config.entity}`;
      return;
    }
    const attributes = stateObj.attributes;
    const files = attributes.source_list || [];
    const selected = attributes.source || "";

    const optionsKey = JSON.stringify(files);
    if (optionsKey !== this._optionsKey) {
      this._optionsKey = optionsKey;
      const placeholder = create("option", { value: "", textContent: "Select a file" });
      e.fileSelect.replaceChildren(
        placeholder,
        ...files.map((name) => create("option", { value: name, textContent: name }))
      );
    }
    e.fileSelect.value = selected;

    const buzzerOptions = attributes.sound_mode_list || [];
    const buzzerKey = JSON.stringify(buzzerOptions);
    if (buzzerKey !== this._buzzerKey) {
      this._buzzerKey = buzzerKey;
      e.buzzerSelect.replaceChildren(
        ...buzzerOptions.map((option) => create("option", { value: option, textContent: option }))
      );
    }
    e.buzzerSelect.value = attributes.sound_mode || "";
    e.buzzerRow.hidden = buzzerOptions.length < 2;

    const state = stateObj.state;
    const labels = { playing: "Playing", paused: "Paused", idle: "Stopped" };
    e.status.replaceChildren(
      `${labels[state] || state}: ${selected || "no file selected"}`,
      ...(attributes.last_error
        ? [create("small", { textContent: attributes.last_error })]
        : [])
    );
    e.play.textContent = state === "paused" ? "Resume" : "Play";
    e.play.disabled = state === "playing" || !selected;
    e.pause.disabled = state !== "playing";
    e.stop.disabled = state === "idle";
    e.remove.disabled = !selected;
    this._updateProgress();
  }

  _currentPosition(stateObj) {
    const attributes = stateObj.attributes;
    let position = attributes.media_position || 0;
    if (stateObj.state === "playing" && attributes.media_position_updated_at) {
      position += (Date.now() - Date.parse(attributes.media_position_updated_at)) / 1000;
    }
    const duration = attributes.media_duration || 0;
    return Math.min(position, duration);
  }

  _updateProgress() {
    const e = this._elements;
    const stateObj = this._stateObject();
    if (!e || !stateObj) {
      return;
    }
    const duration = stateObj.attributes.media_duration || 0;
    const position = this._currentPosition(stateObj);
    e.fill.style.width = duration > 0 ? `${(position / duration) * 100}%` : "0%";
    e.position.textContent = formatTime(position);
    e.duration.textContent = formatTime(duration);
  }

  _seek(event) {
    const stateObj = this._stateObject();
    if (!stateObj || stateObj.state === "idle") {
      return;
    }
    const duration = stateObj.attributes.media_duration || 0;
    const rect = this._elements.bar.getBoundingClientRect();
    if (duration <= 0 || rect.width <= 0) {
      return;
    }
    const fraction = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    this._call("media_player", "media_seek", { seek_position: fraction * duration });
  }

  _message(text, isError) {
    this._elements.message.textContent = text;
    this._elements.message.className = isError ? "message error" : "message";
  }

  async _run(action) {
    try {
      await action();
      this._message("", false);
    } catch (error) {
      this._message(error && error.message ? error.message : String(error), true);
    }
  }

  _call(domain, service, data = {}) {
    return this._run(() =>
      this._hass.callService(domain, service, { entity_id: this._config.entity, ...data })
    );
  }

  async _upload() {
    const e = this._elements;
    const file = e.input.files && e.input.files[0];
    if (!file) {
      this._message("Choose a .mid or .midi file first", true);
      return;
    }
    if (!/\.midi?$/i.test(file.name)) {
      this._message("Only .mid and .midi files are supported", true);
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      this._message(`The file is larger than ${MAX_UPLOAD_BYTES / 1024} KiB`, true);
      return;
    }
    e.upload.disabled = true;
    try {
      const data = toBase64(await file.arrayBuffer());
      const result = await this._hass.callWS({
        type: "midi_player/upload",
        filename: file.name,
        data,
      });
      e.input.value = "";
      await this._hass.callService("media_player", "select_source", {
        entity_id: this._config.entity,
        source: result.filename,
      });
      this._message(`Uploaded ${result.filename}`, false);
    } catch (error) {
      this._message(error && error.message ? error.message : String(error), true);
    } finally {
      e.upload.disabled = false;
    }
  }

  async _delete() {
    const stateObj = this._stateObject();
    const name = stateObj && stateObj.attributes.source;
    if (!name || !window.confirm(`Delete ${name}?`)) {
      return;
    }
    await this._run(() => this._hass.callService("midi_player", "delete_file", { filename: name }));
  }
}

customElements.define("midi-player-card", MidiPlayerCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "midi-player-card",
  name: "MIDI Player",
  description: "Upload, select and play MIDI files on an ESPHome buzzer.",
});
