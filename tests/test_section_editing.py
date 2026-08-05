"""Editing the section list must not answer for the open editor.

The sidebar's section and the section chosen in the editor are two different
things: one is what the list is showing, the other is where the expansion being
edited should end up. Rebuilding the list used to overwrite the second with the
first, so adding, renaming or deleting a section moved an open expansion
somewhere else on the next Apply, with nothing on screen to say so.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

# Run Qt without a real display so the GUI tests work headlessly (e.g. in CI).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import app as app_module
from ahk_manager import Expansion, ExpansionStore
from app import ExpansionApp
from qt_cleanup import destroy_all_windows

# A single QApplication must exist for the lifetime of the process.
_qt_app = QApplication.instance() or QApplication([])


class SectionEditingTests(unittest.TestCase):
    def setUp(self) -> None:
        destroy_all_windows()
        self._temp = TemporaryDirectory()
        root = Path(self._temp.name)
        ExpansionStore(
            sections=["Work", "Personal"],
            expansions=[
                Expansion("Work", ";one", "first"),
                Expansion("Personal", ";two", "second"),
            ],
        ).save(root / "expansions.json")
        self._saved_paths = (
            app_module.JSON_PATH,
            app_module.SETTINGS_PATH,
            app_module.UI_PREFS_PATH,
            app_module.AHK_PATH,
            app_module.DEFAULT_BACKUP_DIR,
        )
        app_module.JSON_PATH = root / "expansions.json"
        app_module.SETTINGS_PATH = root / "settings.json"
        app_module.UI_PREFS_PATH = root / "ui_prefs.json"
        app_module.AHK_PATH = root / "text_expansions.ahk"
        app_module.DEFAULT_BACKUP_DIR = root / "backups"
        self.app = ExpansionApp()

    def tearDown(self) -> None:
        self.app.close()
        (
            app_module.JSON_PATH,
            app_module.SETTINGS_PATH,
            app_module.UI_PREFS_PATH,
            app_module.AHK_PATH,
            app_module.DEFAULT_BACKUP_DIR,
        ) = self._saved_paths
        self._temp.cleanup()

    def open_first_expansion(self) -> None:
        self.app.tree.selectRow(0)
        self.app.load_selected_expansion()

    def add_section(self, name: str) -> None:
        with mock.patch.object(
            app_module.QInputDialog, "getText", return_value=(name, True)
        ):
            self.app.add_section()

    def rename_section(self, name: str) -> None:
        with mock.patch.object(
            app_module.QInputDialog, "getText", return_value=(name, True)
        ):
            self.app.rename_section()

    def test_adding_a_section_leaves_the_open_editor_where_it_was(self) -> None:
        # No deletion needed to hit this: adding a section moves the sidebar to
        # it, and the editor followed silently.
        self.open_first_expansion()

        self.add_section("Temp")

        self.assertEqual(self.app.section_combo.currentText(), "Work")

    def test_the_section_chosen_in_the_editor_survives_a_delete_elsewhere(self) -> None:
        self.open_first_expansion()
        self.add_section("Temp")
        self.app.section_combo.setCurrentText("Personal")

        with mock.patch.object(app_module, "confirm", return_value=True):
            self.app.delete_section()  # deletes Temp, which the sidebar is on

        self.assertEqual(self.app.section_combo.currentText(), "Personal")

    def test_apply_then_files_it_where_the_editor_asked(self) -> None:
        self.open_first_expansion()
        self.add_section("Temp")
        self.app.section_combo.setCurrentText("Personal")

        with mock.patch.object(app_module, "confirm", return_value=True):
            self.app.delete_section()
        self.app.apply_form()

        self.assertEqual(self.app.store.expansions[0].section, "Personal")

    def test_deleting_the_editors_own_section_falls_back(self) -> None:
        # Nothing to preserve: the section the editor named is gone, so the
        # combo must not be left holding a name the list no longer has.
        self.open_first_expansion()  # a Work expansion, combo on Work

        with mock.patch.object(app_module, "confirm", return_value=True):
            self.app.delete_section()  # the sidebar is on Work

        self.assertIn(self.app.section_combo.currentText(), self.app.store.sections)
        self.assertEqual(self.app.section_combo.currentText(), "Personal")

    def test_a_rename_carries_the_open_editor_with_it(self) -> None:
        # The store renames the section on every expansion in it, so the name
        # the editor held is gone and the new one is what it means.
        self.open_first_expansion()

        self.rename_section("Job")

        self.assertEqual(self.app.section_combo.currentText(), "Job")
        self.assertEqual(self.app.store.expansions[0].section, "Job")

    def test_with_nothing_open_the_sidebar_still_drives_the_form(self) -> None:
        # The preserved case is only about an editor holding something; an
        # empty form should still follow the list, as it always has.
        self.app.new_expansion()

        self.add_section("Temp")

        self.assertEqual(self.app.section_combo.currentText(), "Temp")


if __name__ == "__main__":
    unittest.main()
