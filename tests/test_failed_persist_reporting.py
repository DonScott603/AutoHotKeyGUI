"""No mutating handler may report success when the write did not happen.

persist returns False when the user declines to replace an unreadable store,
and every handler has to respect that. Testing a single handler was how the
delete_section path kept reporting `Deleted section "General".` over a file it
had not touched -- so this drives all of them from one table instead.
"""

import os
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable
from unittest import mock

# Run Qt without a real display so the GUI tests work headlessly (e.g. in CI).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QInputDialog

import app as app_module
from ahk_manager import Expansion, TemplateDef, VariableDef
from app import ExpansionApp
from qt_cleanup import destroy_all_windows

# A single QApplication must exist for the lifetime of the process.
_qt_app = QApplication.instance() or QApplication([])

# Malformed but plainly still holding a library, so persist asks before
# replacing it and refuse_replacement below answers No.
RECOVERABLE = '{"expansions": [{"trigger": ";keep", "replacement": "important"}]'


def refuse_replacement(parent: object, title: str, message: str) -> bool:
    """Agree to whatever the handler asks, refuse to replace the store."""
    return title != "Replace unreadable library"


def add_expansion(app: ExpansionApp) -> None:
    app.current_expansion = None
    app.section_combo.setCurrentText("Work")
    app.trigger_edit.setText(";new")
    app.replacement_text.setPlainText("added")
    app.apply_form()


def update_expansion(app: ExpansionApp) -> None:
    app.tree.selectRow(0)
    app.load_selected_expansion()
    app.replacement_text.setPlainText("changed")
    app.apply_form()


def delete_expansion(app: ExpansionApp) -> None:
    app.tree.selectRow(0)
    app.delete_expansion()


def toggle_enabled(app: ExpansionApp) -> None:
    app.tree.selectRow(0)
    app.toggle_enabled()


def apply_variable(app: ExpansionApp) -> None:
    app.current_variable = None
    app.variable_name_edit.setText("fresh")
    app.variable_type_combo.setCurrentText("text_input")
    app.variable_prompt_edit.setText("Prompt")
    app.apply_variable()


def delete_variable(app: ExpansionApp) -> None:
    app.variable_tree.selectRow(0)
    app.delete_variable()


def apply_template(app: ExpansionApp) -> None:
    app.current_template = None
    app.template_name_edit.setText("Fresh")
    app.template_body_text.setPlainText("body")
    app.apply_template()


def delete_template(app: ExpansionApp) -> None:
    app.template_tree.selectRow(0)
    app.delete_template()


def duplicate_template(app: ExpansionApp) -> None:
    app.template_tree.selectRow(0)
    app.duplicate_template()


def add_section(app: ExpansionApp) -> None:
    with mock.patch.object(QInputDialog, "getText", return_value=("Added", True)):
        app.add_section()


def rename_section(app: ExpansionApp) -> None:
    app.selected_section = "Spare"
    with mock.patch.object(QInputDialog, "getText", return_value=("Renamed", True)):
        app.rename_section()


def delete_section(app: ExpansionApp) -> None:
    app.selected_section = "Spare"
    app.delete_section()


# Every handler that mutates the store and then persists. import_ahk is the one
# omission: it opens a file dialog before it reaches persist, and it is covered
# by its own tests in test_import_merge.
HANDLERS: dict[str, Callable[[ExpansionApp], None]] = {
    "add expansion": add_expansion,
    "update expansion": update_expansion,
    "delete expansion": delete_expansion,
    "toggle enabled": toggle_enabled,
    "apply variable": apply_variable,
    "delete variable": delete_variable,
    "apply template": apply_template,
    "delete template": delete_template,
    "duplicate template": duplicate_template,
    "add section": add_section,
    "rename section": rename_section,
    "delete section": delete_section,
}


class FailedPersistReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        destroy_all_windows()
        self._temp = TemporaryDirectory()
        root = Path(self._temp.name)
        self.json_path = root / "expansions.json"
        self._saved_paths = (
            app_module.JSON_PATH,
            app_module.SETTINGS_PATH,
            app_module.AHK_PATH,
            app_module.DEFAULT_BACKUP_DIR,
        )
        app_module.JSON_PATH = self.json_path
        app_module.SETTINGS_PATH = root / "settings.json"
        app_module.AHK_PATH = root / "text_expansions.ahk"
        app_module.DEFAULT_BACKUP_DIR = root / "backups"

    def tearDown(self) -> None:
        (
            app_module.JSON_PATH,
            app_module.SETTINGS_PATH,
            app_module.AHK_PATH,
            app_module.DEFAULT_BACKUP_DIR,
        ) = self._saved_paths
        self._temp.cleanup()

    @contextmanager
    def window(self):
        """One window for the whole table, reset between handlers.

        Deliberately not one window per handler. Each ExpansionApp is a lot of
        Qt, and building a dozen per test method was enough to exhaust the
        process partway through the full suite -- which hangs a later test
        rather than failing this one.
        """
        with mock.patch.object(app_module, "show_error"):
            app = ExpansionApp()
        try:
            yield app
        finally:
            # closeEvent asks about unsaved changes, and that dialog is modal,
            # so a headless run would block on it rather than fail.
            app._set_unsaved(False)
            app.close()
            app.deleteLater()
            _qt_app.processEvents()

    def reset(self, app: ExpansionApp) -> None:
        """Back to the state of a window just opened on the unreadable file.

        The store stood in empty because the file would not load, so a library
        is filled in here: the handlers need something to act on, and persist
        still refuses to write over the file.
        """
        self.json_path.write_text(RECOVERABLE, encoding="utf-8")
        app._store_unreadable = True
        app._session_backed_up = False
        app._set_unsaved(False)
        app.set_status("Ready.")
        app.store.sections[:] = ["Work", "Spare"]
        app.store.expansions[:] = [Expansion("Work", ";a", "text")]
        app.store.variables[:] = [VariableDef("v", "text_input", "Prompt", "", [], "")]
        app.store.templates[:] = [TemplateDef("T", "", "body", "")]
        app.current_expansion = None
        app.current_variable = None
        app.current_template = None
        app.selected_section = "Work"
        app.refresh_sections()
        app.refresh_expansions()
        app.refresh_variables()
        app.refresh_templates()

    def test_no_handler_reports_success_over_an_untouched_file(self) -> None:
        with self.window() as app:
            for label, mutate in HANDLERS.items():
                with self.subTest(label):
                    self.reset(app)
                    with mock.patch.object(app_module, "confirm", refuse_replacement):
                        mutate(app)

                    status = app.status_label.text()
                    self.assertIn(
                        "nothing was saved",
                        status,
                        f"{label} reported {status!r} over a file it did not write",
                    )
                    self.assertEqual(
                        self.json_path.read_text(encoding="utf-8"), RECOVERABLE
                    )

    def test_every_handler_leaves_the_unsaved_marker(self) -> None:
        with self.window() as app:
            for label, mutate in HANDLERS.items():
                with self.subTest(label):
                    self.reset(app)
                    with mock.patch.object(app_module, "confirm", refuse_replacement):
                        mutate(app)

                    self.assertTrue(app._unsaved_changes, label)
                    self.assertEqual(app.unsaved_label.text(), "Unsaved changes")

    def test_every_handler_still_reports_a_write_that_happened(self) -> None:
        # The mirror image: the guard must not have silenced the normal case.
        with self.window() as app:
            for label, mutate in HANDLERS.items():
                with self.subTest(label):
                    self.reset(app)
                    with mock.patch.object(app_module, "confirm", return_value=True):
                        mutate(app)

                    self.assertNotIn("nothing was saved", app.status_label.text())
                    self.assertFalse(app._unsaved_changes, label)


if __name__ == "__main__":
    unittest.main()
