"""The app's own files live in a config folder beside the executable.

They used to sit loose next to the exe, which left the install folder holding
four files that were none of the user's business. An install carrying the old
layout has them moved across once, on the first run of a build that expects the
new one -- before anything is read, because loading first would find an empty
config folder and start blank with the real library one level up.
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
from ahk_manager import AHK_CONFIG_DIR_NAME, Expansion, ExpansionStore
from app import ExpansionApp
from qt_cleanup import destroy_all_windows

# A single QApplication must exist for the lifetime of the process.
_qt_app = QApplication.instance() or QApplication([])


class ConfigMigrationTests(unittest.TestCase):
    """Drives migrate_config_files directly, without building a window."""

    def setUp(self) -> None:
        destroy_all_windows()
        self._temp = TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.config = self.root / AHK_CONFIG_DIR_NAME
        self._saved = (
            app_module.JSON_PATH,
            app_module.SETTINGS_PATH,
            app_module.UI_PREFS_PATH,
        )
        app_module.JSON_PATH = self.config / "expansions.json"
        app_module.SETTINGS_PATH = self.config / "settings.json"
        app_module.UI_PREFS_PATH = self.config / "ui_prefs.json"

    def tearDown(self) -> None:
        (
            app_module.JSON_PATH,
            app_module.SETTINGS_PATH,
            app_module.UI_PREFS_PATH,
        ) = self._saved
        self._temp.cleanup()

    def test_the_old_layout_is_moved_in(self) -> None:
        (self.root / "expansions.json").write_text('{"expansions": []}', encoding="utf-8")
        (self.root / "settings.json").write_text("{}", encoding="utf-8")
        (self.root / "ui_prefs.json").write_text('{"theme": "dark"}', encoding="utf-8")

        moved, failed = app_module.migrate_config_files()

        self.assertEqual(sorted(moved), ["expansions.json", "settings.json", "ui_prefs.json"])
        self.assertEqual(failed, [])
        self.assertTrue((self.config / "expansions.json").is_file())
        self.assertFalse((self.root / "expansions.json").exists(), "the original was left")
        self.assertEqual(
            json.loads((self.config / "ui_prefs.json").read_text(encoding="utf-8")),
            {"theme": "dark"},
        )

    def test_a_file_already_in_the_config_folder_wins(self) -> None:
        # The one outside is then a leftover, not the library. Moving it over
        # the top would lose whatever the app has been using.
        self.config.mkdir()
        (self.config / "expansions.json").write_text('{"sections": ["Kept"]}', encoding="utf-8")
        (self.root / "expansions.json").write_text('{"sections": ["Stale"]}', encoding="utf-8")

        moved, failed = app_module.migrate_config_files()

        self.assertEqual(moved, [])
        self.assertEqual(failed, [])
        self.assertIn("Kept", (self.config / "expansions.json").read_text(encoding="utf-8"))
        self.assertTrue((self.root / "expansions.json").is_file())

    def test_nothing_to_move_is_not_reported(self) -> None:
        self.assertEqual(app_module.migrate_config_files(), ([], []))

    def test_a_library_outside_a_config_folder_stops_the_whole_migration(self) -> None:
        # The guard that keeps this off the real install folder: every fixture
        # here redirects the library somewhere flat, and the migration has to
        # follow it rather than deciding file by file. Settings still points
        # into a config folder, and must be left alone anyway.
        app_module.JSON_PATH = self.root / "expansions.json"
        (self.root / "expansions.json").write_text("{}", encoding="utf-8")
        (self.root / "settings.json").write_text("{}", encoding="utf-8")

        moved, failed = app_module.migrate_config_files()

        self.assertEqual((moved, failed), ([], []))
        self.assertTrue((self.root / "settings.json").is_file())
        self.assertFalse(self.config.exists())

    def test_a_file_redirected_out_of_the_config_folder_is_skipped(self) -> None:
        app_module.UI_PREFS_PATH = self.root / "elsewhere" / "ui_prefs.json"
        (self.root / "expansions.json").write_text("{}", encoding="utf-8")
        (self.root / "ui_prefs.json").write_text("{}", encoding="utf-8")

        moved, _ = app_module.migrate_config_files()

        self.assertEqual(moved, ["expansions.json"])
        self.assertTrue((self.root / "ui_prefs.json").is_file(), "it was not ours to move")

    def test_a_move_that_fails_is_reported_and_leaves_the_original(self) -> None:
        (self.root / "expansions.json").write_text('{"expansions": []}', encoding="utf-8")

        with mock.patch.object(app_module.shutil, "move", side_effect=OSError("denied")):
            moved, failed = app_module.migrate_config_files()

        self.assertEqual(moved, [])
        self.assertEqual(failed, ["expansions.json"])
        self.assertTrue((self.root / "expansions.json").is_file(), "the original was lost")


class ConfigLayoutTests(unittest.TestCase):
    """The window itself, built over an install in the old layout."""

    def setUp(self) -> None:
        destroy_all_windows()
        self._temp = TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.config = self.root / AHK_CONFIG_DIR_NAME
        ExpansionStore(
            sections=["Work"],
            expansions=[Expansion("Work", ";seed", "seed text")],
        ).save(self.root / "expansions.json")
        self._saved = (
            app_module.JSON_PATH,
            app_module.SETTINGS_PATH,
            app_module.UI_PREFS_PATH,
            app_module.AHK_PATH,
            app_module.DEFAULT_BACKUP_DIR,
        )
        app_module.JSON_PATH = self.config / "expansions.json"
        app_module.SETTINGS_PATH = self.config / "settings.json"
        app_module.UI_PREFS_PATH = self.config / "ui_prefs.json"
        app_module.AHK_PATH = self.root / "text_expansions.ahk"
        app_module.DEFAULT_BACKUP_DIR = self.root / "backups"
        self.app: ExpansionApp | None = None

    def tearDown(self) -> None:
        if self.app is not None:
            self.app.close()
        (
            app_module.JSON_PATH,
            app_module.SETTINGS_PATH,
            app_module.UI_PREFS_PATH,
            app_module.AHK_PATH,
            app_module.DEFAULT_BACKUP_DIR,
        ) = self._saved
        self._temp.cleanup()

    def test_startup_moves_the_library_in_and_loads_it(self) -> None:
        # Loading before the move would find nothing and open blank over the
        # top of a library that was one folder away the whole time.
        self.app = ExpansionApp()

        self.assertEqual([e.trigger for e in self.app.store.expansions], [";seed"])
        self.assertTrue((self.config / "expansions.json").is_file())
        self.assertFalse((self.root / "expansions.json").exists())

    def test_the_move_is_reported_in_the_status_bar(self) -> None:
        self.app = ExpansionApp()

        self.assertIn(AHK_CONFIG_DIR_NAME, self.app.status_label.text())
        self.assertIn("expansions.json", self.app.status_label.text())

    def test_saving_creates_the_config_folder_when_there_is_nothing_to_move(self) -> None:
        (self.root / "expansions.json").unlink()
        self.app = ExpansionApp()
        self.assertFalse(self.config.exists(), "nothing should be created before a save")

        self.app.persist()

        self.assertTrue((self.config / "expansions.json").is_file())


if __name__ == "__main__":
    unittest.main()
