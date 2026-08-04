"""The Variables form shows only what the selected type reads.

Every type ignores the other types' value fields -- a date_time formats the
clock and never prompts, a list_selection takes its default from the first
option -- so a form that showed all of them invited values that were never
generated from, and stored them where they could resurface on a later type
change.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

# Run Qt without a real display so the GUI tests work headlessly (e.g. in CI).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import app as app_module
from ahk_manager import DEFAULT_DATE_FORMAT, ExpansionStore, VariableDef
from app import ExpansionApp

# A single QApplication must exist for the lifetime of the process.
_qt_app = QApplication.instance() or QApplication([])


class VariableTypeFormTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        root = Path(self._temp.name)
        ExpansionStore(
            sections=["Work"],
            variables=[
                VariableDef("client", "text_input", "Client", "Acme", [], ""),
                VariableDef("status", "list_selection", "Status", "", ["New", "Done"], ""),
                VariableDef("today", "date_time", "", "yyyy-MM-dd", [], ""),
            ],
        ).save(root / "expansions.json")
        self._saved_paths = (
            app_module.JSON_PATH,
            app_module.SETTINGS_PATH,
            app_module.AHK_PATH,
            app_module.DEFAULT_BACKUP_DIR,
        )
        app_module.JSON_PATH = root / "expansions.json"
        app_module.SETTINGS_PATH = root / "settings.json"
        app_module.AHK_PATH = root / "text_expansions.ahk"
        app_module.DEFAULT_BACKUP_DIR = root / "backups"
        self.app = ExpansionApp()

    def tearDown(self) -> None:
        self.app.close()
        (
            app_module.JSON_PATH,
            app_module.SETTINGS_PATH,
            app_module.AHK_PATH,
            app_module.DEFAULT_BACKUP_DIR,
        ) = self._saved_paths
        self._temp.cleanup()

    def choose(self, variable_type: str) -> None:
        self.app.variable_type_combo.setCurrentText(variable_type)

    # isHidden reads the explicit show/hide the form applied. isVisibleTo is no
    # use here: the window is never shown, so everything on a stacked page
    # counts as invisible and every assertion would pass.
    def assertShown(self, name: str) -> None:
        self.assertFalse(getattr(self.app, name).isHidden(), f"{name} is not on screen")

    def assertHidden(self, name: str) -> None:
        self.assertTrue(getattr(self.app, name).isHidden(), f"{name} is still on screen")

    # -- which boxes are on screen -----------------------------------------
    def test_text_input_hides_the_options_box(self) -> None:
        self.choose("text_input")

        self.assertShown("variable_prompt_edit")
        self.assertShown("variable_default_edit")
        self.assertEqual(self.app.variable_default_label.text(), "Default")
        self.assertHidden("variable_options_text")
        self.assertHidden("variable_options_label")
        self.assertIn("optional", self.app.variable_options_note.text())

    def test_list_selection_hides_the_default_box(self) -> None:
        self.choose("list_selection")

        self.assertHidden("variable_default_edit")
        self.assertShown("variable_options_text")
        note = self.app.variable_default_note.text()
        self.assertShown("variable_default_note")
        self.assertIn("first list option", note)
        self.assertIn("one option per line", note.lower())

    def test_date_time_hides_the_prompt_and_the_options_box(self) -> None:
        self.choose("date_time")

        self.assertHidden("variable_prompt_edit")
        self.assertHidden("variable_prompt_label")
        self.assertEqual(self.app.variable_default_label.text(), "Format")
        self.assertShown("variable_default_edit")
        self.assertHidden("variable_options_text")
        note = self.app.variable_options_note.text()
        self.assertIn("yyyy", note)
        self.assertIn("MMMM", note)
        self.assertIn("tt", note)
        self.assertIn(DEFAULT_DATE_FORMAT, note)

    def test_selecting_a_variable_lays_the_form_out_for_its_type(self) -> None:
        self.app.variable_tree.selectRow(2)  # the date_time one

        current = self.app.current_variable
        assert current is not None, "selecting a row did not load it"
        self.assertEqual(current.name, "today")
        self.assertEqual(self.app.variable_default_label.text(), "Format")
        self.assertHidden("variable_prompt_edit")

    # -- what gets saved ---------------------------------------------------
    def test_a_type_change_drops_the_fields_the_new_type_ignores(self) -> None:
        # The list options are still in the boxes, hidden, when the type
        # becomes text_input; saving them would store what cannot be seen.
        self.app.variable_tree.selectRow(1)  # status, a list_selection
        self.choose("text_input")
        self.app.variable_default_edit.setText("Pending")

        self.app.apply_variable()

        saved = self.app.store.variables[1]
        self.assertEqual(saved.type, "text_input")
        self.assertEqual(saved.default_value, "Pending")
        self.assertEqual(saved.list_options, [])

    def test_a_list_selection_saves_no_default(self) -> None:
        self.app.variable_tree.selectRow(0)  # client, a text_input with a default
        self.choose("list_selection")
        self.app.variable_options_text.setPlainText("New\nDone")

        self.app.apply_variable()

        saved = self.app.store.variables[0]
        self.assertEqual(saved.list_options, ["New", "Done"])
        self.assertEqual(saved.default_value, "")

    def test_a_date_time_saves_no_prompt(self) -> None:
        self.app.variable_tree.selectRow(0)  # client, which has a prompt
        self.choose("date_time")
        self.app.variable_default_edit.setText("h:mm tt")

        self.app.apply_variable()

        saved = self.app.store.variables[0]
        self.assertEqual(saved.default_value, "h:mm tt")
        self.assertEqual(saved.prompt_text, "")

    # -- preview -----------------------------------------------------------
    def test_the_preview_shows_only_the_fields_the_type_reads(self) -> None:
        from ahk_manager import resolve_variable_preview

        text = resolve_variable_preview(self.app.store.variables[2]).content
        self.assertIn("Format: yyyy-MM-dd", text)
        self.assertNotIn("Prompt text:", text)
        self.assertNotIn("List Options", text)

        text = resolve_variable_preview(self.app.store.variables[1]).content
        self.assertIn("List Options", text)
        self.assertIn("The first option is the default.", text)
        self.assertNotIn("Default value:", text)

        text = resolve_variable_preview(self.app.store.variables[0]).content
        self.assertIn("Prompt text: Client", text)
        self.assertIn("Default value: Acme", text)
        self.assertNotIn("List Options", text)

    def test_a_blank_format_previews_as_what_is_generated(self) -> None:
        from ahk_manager import resolve_variable_preview

        preview = resolve_variable_preview(VariableDef("stamp", "date_time"))

        self.assertIn(f"Format: {DEFAULT_DATE_FORMAT}", preview.content)
        self.assertIn(f'FormatTime(A_Now, "{DEFAULT_DATE_FORMAT}")', preview.content)


if __name__ == "__main__":
    unittest.main()
