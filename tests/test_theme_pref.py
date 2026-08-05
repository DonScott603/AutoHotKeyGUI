"""An unreadable ui_prefs.json must not stop the window opening.

The theme is read before the UI is built, so an exception there is a crash box
with nothing behind it -- and unlike expansions.json there is no backup of this
file and no way to repair it from inside the application. Every shape it can
hold has to resolve to "no preference".
"""

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

# Run Qt without a real display so the GUI tests work headlessly (e.g. in CI).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import app as app_module
from app import ExpansionApp, load_theme_pref, save_theme_pref
from qt_cleanup import destroy_all_windows

# A single QApplication must exist for the lifetime of the process.
_qt_app = QApplication.instance() or QApplication([])

# Valid JSON that is not an object. Each parses cleanly and then reaches .get,
# which is what raised AttributeError before the window existed.
NON_OBJECT_JSON = [
    ("null", "null"),
    ("an array", "[]"),
    ("a boolean", "true"),
    ("a number", "42"),
    ("a string", '"dark"'),
]


class ThemePrefTests(unittest.TestCase):
    def setUp(self) -> None:
        destroy_all_windows()
        self._temp = TemporaryDirectory()
        self.path = Path(self._temp.name) / "ui_prefs.json"
        self._saved = app_module.UI_PREFS_PATH
        app_module.UI_PREFS_PATH = self.path

    def tearDown(self) -> None:
        app_module.UI_PREFS_PATH = self._saved
        self._temp.cleanup()

    def test_valid_json_that_is_not_an_object_has_no_preference(self) -> None:
        for label, content in NON_OBJECT_JSON:
            with self.subTest(label):
                self.path.write_text(content, encoding="utf-8")

                self.assertIsNone(load_theme_pref())

    def test_malformed_json_has_no_preference(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")

        self.assertIsNone(load_theme_pref())

    def test_a_missing_file_has_no_preference(self) -> None:
        self.assertIsNone(load_theme_pref())

    def test_an_object_without_a_theme_has_no_preference(self) -> None:
        self.path.write_text('{"something": "else"}', encoding="utf-8")

        self.assertIsNone(load_theme_pref())

    def test_an_unrecognised_theme_has_no_preference(self) -> None:
        for value in ("purple", 5, None, ["dark"]):
            with self.subTest(repr(value)):
                self.path.write_text(json.dumps({"theme": value}), encoding="utf-8")

                self.assertIsNone(load_theme_pref())

    def test_a_saved_theme_is_read_back(self) -> None:
        for theme in ("light", "dark"):
            with self.subTest(theme):
                save_theme_pref(theme)

                self.assertEqual(load_theme_pref(), theme)


class ThemePrefStartupTests(unittest.TestCase):
    """The payoff: the window opens whatever the file holds."""

    def setUp(self) -> None:
        destroy_all_windows()
        self._temp = TemporaryDirectory()
        root = Path(self._temp.name)
        self._saved_paths = (
            app_module.JSON_PATH,
            app_module.SETTINGS_PATH,
            app_module.AHK_PATH,
            app_module.DEFAULT_BACKUP_DIR,
            app_module.UI_PREFS_PATH,
        )
        app_module.JSON_PATH = root / "expansions.json"
        app_module.SETTINGS_PATH = root / "settings.json"
        app_module.AHK_PATH = root / "text_expansions.ahk"
        app_module.DEFAULT_BACKUP_DIR = root / "backups"
        app_module.UI_PREFS_PATH = root / "ui_prefs.json"
        self.prefs_path = app_module.UI_PREFS_PATH

    def tearDown(self) -> None:
        (
            app_module.JSON_PATH,
            app_module.SETTINGS_PATH,
            app_module.AHK_PATH,
            app_module.DEFAULT_BACKUP_DIR,
            app_module.UI_PREFS_PATH,
        ) = self._saved_paths
        self._temp.cleanup()

    def test_the_window_opens_on_a_preference_file_of_any_shape(self) -> None:
        for label, content in NON_OBJECT_JSON:
            with self.subTest(label):
                self.prefs_path.write_text(content, encoding="utf-8")

                with mock.patch.object(app_module, "show_error"):
                    window = ExpansionApp()
                try:
                    self.assertIn(window.theme, ("light", "dark"))
                finally:
                    window._set_unsaved(False)
                    window.close()
                    window.deleteLater()
                    _qt_app.processEvents()


if __name__ == "__main__":
    unittest.main()
