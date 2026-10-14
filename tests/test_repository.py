import ast
import io
import json
import re
import tokenize
import unittest

import support
from support import COMPONENT_DIR, ROOT

try:
    import yaml
except ImportError:
    yaml = None

from custom_components.midi_player import const

PACKAGE_FILE = ROOT / "esphome" / "midi_player.yaml"
EXAMPLE_FILE = ROOT / "esphome" / "example.yaml"
CARD_FILE = COMPONENT_DIR / "frontend" / "midi-player-card.js"


def load_yaml(path):
    class Loader(yaml.SafeLoader):
        pass

    for tag in ("!secret", "!lambda", "!include", "!extend", "!remove"):
        Loader.add_constructor(tag, lambda loader, node: loader.construct_scalar(node))
    return yaml.load(path.read_text(), Loader=Loader)


class MetadataTests(unittest.TestCase):
    def test_manifest(self):
        manifest = json.loads((COMPONENT_DIR / "manifest.json").read_text())
        self.assertEqual(manifest["domain"], const.DOMAIN)
        self.assertEqual(COMPONENT_DIR.name, manifest["domain"])
        for key in ("name", "codeowners", "documentation", "issue_tracker", "version", "config_flow", "iot_class"):
            self.assertIn(key, manifest)
        self.assertTrue(manifest["config_flow"])
        for dependency in ("http", "websocket_api", "frontend"):
            self.assertIn(dependency, manifest["dependencies"])
        self.assertEqual(manifest["requirements"], [])
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(manifest["version"], const.CARD_VERSION)
        keys = list(manifest)
        self.assertEqual(keys[:2], ["domain", "name"])
        self.assertEqual(keys[2:], sorted(keys[2:]))

    def test_hacs_json(self):
        hacs = json.loads((ROOT / "hacs.json").read_text())
        self.assertEqual(hacs["name"], "MIDI Player")
        self.assertRegex(hacs["homeassistant"], r"^\d{4}\.\d+\.\d+$")

    def test_required_repository_files_exist(self):
        for relative in (
            "README.md",
            "LICENSE",
            "hacs.json",
            "requirements-test.txt",
            "custom_components/midi_player/__init__.py",
            "custom_components/midi_player/frontend/midi-player-card.js",
            "esphome/midi_player.yaml",
            "esphome/example.yaml",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_only_one_integration_directory(self):
        names = [p.name for p in (ROOT / "custom_components").iterdir() if p.is_dir() and p.name != "__pycache__"]
        self.assertEqual(names, ["midi_player"])

    def test_every_python_module_is_used(self):
        sources = {path.stem: path.read_text() for path in COMPONENT_DIR.glob("*.py")}
        entry_points = {"__init__", "config_flow", "media_player"}
        for name in sources:
            if name in entry_points:
                continue
            pattern = re.compile(rf"from \.{name} import|from \. import {name}")
            self.assertTrue(
                any(pattern.search(text) for other, text in sources.items() if other != name),
                f"{name} is never imported",
            )

    def test_translations_cover_services_and_flow(self):
        translations = json.loads((COMPONENT_DIR / "translations" / "en.json").read_text())
        services = yaml.safe_load((COMPONENT_DIR / "services.yaml").read_text())
        self.assertEqual(set(services), set(translations["services"]))
        self.assertEqual(
            set(services),
            {const.SERVICE_PLAY_FILE, const.SERVICE_DELETE_FILE, const.SERVICE_RESCAN_LIBRARY},
        )
        for name, definition in services.items():
            expected = set((definition or {}).get("fields", {}))
            self.assertEqual(expected, set(translations["services"][name].get("fields", {})), name)
        for error in ("invalid_device", "actions_missing"):
            self.assertIn(error, translations["config"]["error"])
        for reason in ("already_configured", "no_esphome_devices", "reconfigure_successful"):
            self.assertIn(reason, translations["config"]["abort"])
        user_fields = set(translations["config"]["step"]["user"]["data"])
        self.assertEqual(user_fields, {const.CONF_ESPHOME_ENTRY_ID, const.CONF_BUZZER_COUNT})
        self.assertEqual(
            set(translations["config"]["step"]["reconfigure"]["data"]), {const.CONF_BUZZER_COUNT}
        )

    def test_buzzer_selector_range_matches_constant(self):
        services = yaml.safe_load((COMPONENT_DIR / "services.yaml").read_text())
        number = services["play_file"]["fields"]["buzzer"]["selector"]["number"]
        self.assertEqual((number["min"], number["max"]), (1, const.MAX_BUZZERS))

    def test_card_uses_the_websocket_command_and_element_name(self):
        card = CARD_FILE.read_text()
        self.assertIn(f'"{const.WS_UPLOAD}"', card)
        self.assertIn('customElements.define("midi-player-card"', card)
        self.assertIn(f"{const.MAX_FILE_SIZE // 1024} KiB", f"{const.MAX_FILE_SIZE // 1024} KiB")
        self.assertIn("1024 * 1024", card)
        self.assertEqual(const.MAX_FILE_SIZE, 1024 * 1024)
        self.assertIn("select_sound_mode", card)

    def test_frontend_is_registered_automatically(self):
        source = (COMPONENT_DIR / "__init__.py").read_text()
        self.assertIn("add_extra_js_url", source)
        self.assertIn("CARD_FILENAME", source)
        self.assertTrue((COMPONENT_DIR / "frontend" / const.CARD_FILENAME).is_file())

    def test_readme_describes_the_published_paths(self):
        readme = (ROOT / "README.md").read_text()
        self.assertIn("esphome/midi_player.yaml", readme)
        self.assertIn("midi-player-card", readme)
        self.assertNotIn("your-github-user", readme)


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class EsphomePackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = PACKAGE_FILE.read_text()
        cls.config = load_yaml(PACKAGE_FILE)

    def test_does_not_touch_the_device_hardware_configuration(self):
        allowed = {"substitutions", "api", "globals", "script", "interval"}
        self.assertEqual(set(self.config), allowed)
        self.assertNotIn("esp32", self.config)
        self.assertNotIn("wifi", self.config)
        self.assertNotIn("framework", self.text)
        self.assertNotRegex(self.text, r"GPIO\d+")

    def test_actions_match_the_integration(self):
        actions = {a["action"]: a for a in self.config["api"]["actions"]}
        self.assertEqual(set(actions), {const.ACTION_PLAY_CHUNK, const.ACTION_STOP})
        self.assertEqual(
            actions[const.ACTION_PLAY_CHUNK]["variables"],
            {"buzzer": "int", "clear_queue": "bool", "frequencies": "float[]", "durations": "int[]"},
        )
        self.assertNotIn("encryption", self.config["api"])

    def test_integration_sends_exactly_the_declared_arguments(self):
        source = (COMPONENT_DIR / "manager.py").read_text()
        for name in ("buzzer", "clear_queue", "frequencies", "durations"):
            self.assertIn(f'"{name}"', source)

    def test_one_tone_script_per_supported_buzzer(self):
        scripts = {item["id"] for item in self.config["script"]}
        expected = {f"midi_tone_{n}" for n in range(1, const.MAX_BUZZERS + 1)} | {"midi_silence"}
        self.assertEqual(scripts, expected)
        for number in range(1, const.MAX_BUZZERS + 1):
            self.assertIn(f"${{midi_buzzer_{number}}}", self.text)
            self.assertIn(f"script.execute: midi_tone_{number}", self.text)
        self.assertIn(f"buzzer <= {const.MAX_BUZZERS}", self.text)

    def test_substitutions_have_defaults_and_chain_to_the_first_buzzer(self):
        substitutions = self.config["substitutions"]
        self.assertEqual(substitutions["midi_buzzer_1"], "buzzer_1")
        for number in range(2, const.MAX_BUZZERS + 1):
            self.assertEqual(substitutions[f"midi_buzzer_{number}"], "${midi_buzzer_1}")
        used = set(re.findall(r"\$\{(\w+)\}", self.text))
        self.assertTrue(used <= set(substitutions), used - set(substitutions))

    def test_referenced_ids_exist(self):
        defined = {item["id"] for section in ("globals", "script") for item in self.config[section]}
        for match in re.finditer(r"id\((\w+)\)", self.text):
            self.assertIn(match.group(1), defined, match.group(1))
        for match in re.finditer(r"script\.execute:\s*(\w+)", self.text):
            self.assertIn(match.group(1), defined, match.group(1))

    def test_tick_interval_is_short(self):
        self.assertEqual(self.config["interval"][0]["interval"], "2ms")

    def test_device_queue_limit_matches_streaming_limits(self):
        from custom_components.midi_player.engine import CHUNK_SIZE, LOOKAHEAD_MS
        from custom_components.midi_player.melody import MIN_SEGMENT_MS

        self.assertIn("< 2048", self.text)
        self.assertLess(LOOKAHEAD_MS // MIN_SEGMENT_MS + CHUNK_SIZE, 2048)

    def test_frequency_limits_are_playable_by_the_device(self):
        from custom_components.midi_player.melody import MAX_FREQUENCY, MIN_FREQUENCY

        self.assertGreaterEqual(MIN_FREQUENCY, 20.0)
        self.assertIn("20.0f", self.text)
        self.assertLessEqual(MAX_FREQUENCY, 20000.0)

    def test_example_configuration(self):
        example = load_yaml(EXAMPLE_FILE)
        self.assertEqual(example["esp32"]["framework"]["type"], "esp-idf")
        outputs = {item["id"] for item in example["output"]}
        for name in ("midi_buzzer_1", "midi_buzzer_2"):
            self.assertIn(example["substitutions"][name], outputs)
        self.assertIn("midi_player", example["packages"])
        self.assertTrue(example["packages"]["midi_player"].endswith("/esphome/midi_player.yaml@main"))
        self.assertIn("!secret", EXAMPLE_FILE.read_text())


class CodeStyleTests(unittest.TestCase):
    def python_files(self):
        files = list(COMPONENT_DIR.glob("*.py")) + list((ROOT / "tests").glob("*.py"))
        self.assertTrue(files)
        return files

    def test_python_files_parse(self):
        for path in self.python_files():
            ast.parse(path.read_text(), filename=str(path))

    def test_python_files_have_no_comments_or_docstrings(self):
        for path in self.python_files():
            source = path.read_text()
            for token in tokenize.generate_tokens(io.StringIO(source).readline):
                self.assertNotEqual(token.type, tokenize.COMMENT, f"{path.name}:{token.start[0]}")
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.assertIsNone(ast.get_docstring(node), f"{path.name}:{getattr(node, 'name', 'module')}")

    def test_other_source_files_have_no_comments(self):
        for path in (
            PACKAGE_FILE,
            EXAMPLE_FILE,
            COMPONENT_DIR / "services.yaml",
            ROOT / "examples" / "automation.yaml",
            ROOT / ".github" / "workflows" / "validate.yaml",
        ):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                self.assertFalse(line.lstrip().startswith("#"), f"{path.name}:{number}")
        self.assertIsNone(re.search(r"^\s*(//|/\*)", CARD_FILE.read_text(), re.MULTILINE))

    def test_no_hardcoded_addresses_or_personal_data(self):
        for path in list(COMPONENT_DIR.glob("*.py")) + [PACKAGE_FILE, EXAMPLE_FILE]:
            text = path.read_text()
            self.assertIsNone(re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", text), path.name)
            self.assertNotIn("tux", text.lower(), path.name)

    def test_no_device_specific_files_remain(self):
        for path in ROOT.rglob("*"):
            if "__pycache__" in path.parts or ".git" in path.parts:
                continue
            self.assertNotIn("tux", path.name.lower())


@unittest.skipIf(not support.HOMEASSISTANT_AVAILABLE, "Home Assistant is not installed")
class HomeAssistantImportTests(unittest.TestCase):
    def test_modules_import(self):
        import importlib

        for module in ("", ".config_flow", ".media_player", ".websocket", ".manager"):
            importlib.import_module(f"custom_components.midi_player{module}")


if __name__ == "__main__":
    unittest.main()
