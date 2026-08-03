"""Renaming or deleting a library item must not strand its references.

A variable or template is referenced by name from expansion text, so the
definition and its uses are only connected by that string. Editing the
definition alone left every {VAR:old} in the library undefined -- a state that
validated nowhere, autosaved without complaint, and surfaced at Generate & Run
long after the edit that caused it.
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
from ahk_manager import (
    Expansion,
    ExpansionStore,
    TemplateDef,
    VariableDef,
    find_references,
    render_ahk,
    rename_in_text,
    rename_references,
    validate_template,
)
from app import ExpansionApp

# A single QApplication must exist for the lifetime of the process.
_qt_app = QApplication.instance() or QApplication([])


def seeded_store() -> ExpansionStore:
    return ExpansionStore(
        sections=["Work"],
        expansions=[
            Expansion("Work", ";hi", "Dear {VAR:client_name},"),
            Expansion("Work", ";bye", "{TPL:Signoff}"),
            Expansion("Work", ";plain", "no placeholders here"),
        ],
        variables=[VariableDef("client_name", "text_input", "Client", "", [], "")],
        templates=[TemplateDef("Signoff", body="Regards, {VAR:client_name}")],
    )


class ReferenceScanTests(unittest.TestCase):
    def test_references_are_found_in_expansions_and_templates(self) -> None:
        users = find_references(seeded_store(), "VAR", "client_name")

        self.assertEqual(users, ['expansion ";hi"', 'template "Signoff"'])

    def test_an_unused_name_has_no_references(self) -> None:
        self.assertEqual(find_references(seeded_store(), "VAR", "unused"), [])

    def test_the_two_kinds_do_not_collide(self) -> None:
        # A variable and a template may share a name; {VAR:x} and {TPL:x} are
        # still different references.
        store = ExpansionStore(
            expansions=[Expansion("Work", ";a", "{VAR:same} {TPL:same}")],
        )

        self.assertEqual(find_references(store, "TPL", "same"), ['expansion ";a"'])

    def test_a_malformed_body_elsewhere_does_not_hide_a_reference(self) -> None:
        # Scanned rather than parsed: this is asked while deciding whether a
        # delete is safe, which is exactly when a library may be half-broken.
        store = ExpansionStore(
            expansions=[Expansion("Work", ";a", "{AHK_INPUT:bad} {VAR:client_name}")],
        )

        self.assertEqual(find_references(store, "VAR", "client_name"), ['expansion ";a"'])


class RenameTextTests(unittest.TestCase):
    def test_only_the_named_reference_changes(self) -> None:
        text = "{VAR:a} {VAR:ab} {VAR:b} {TPL:a}"

        self.assertEqual(rename_in_text(text, "VAR", "a", "z"), "{VAR:z} {VAR:ab} {VAR:b} {TPL:a}")

    def test_surrounding_text_is_returned_byte_for_byte(self) -> None:
        text = "Dear {VAR:a},\n\n  indented {AHK_INPUT:n|Prompt|Title|d} end"

        self.assertEqual(
            rename_in_text(text, "VAR", "a", "b"),
            "Dear {VAR:b},\n\n  indented {AHK_INPUT:n|Prompt|Title|d} end",
        )

    def test_every_occurrence_is_renamed(self) -> None:
        self.assertEqual(rename_in_text("{VAR:a}{VAR:a}", "VAR", "a", "b"), "{VAR:b}{VAR:b}")

    def test_renaming_the_store_reports_what_changed(self) -> None:
        store = seeded_store()

        changed = rename_references(store, "VAR", "client_name", "client")

        self.assertEqual(changed, 2)
        self.assertEqual(store.expansions[0].replacement, "Dear {VAR:client},")
        self.assertEqual(store.templates[0].body, "Regards, {VAR:client}")
        self.assertEqual(store.expansions[2].replacement, "no placeholders here")


class _Window:
    """Build the window against a temporary library."""

    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        root = Path(self._temp.name)
        self.json_path = root / "expansions.json"
        seeded_store().save(self.json_path)
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

    def rename_variable(self, new_name: str) -> None:
        variable = self.app.store.variables[0]
        self.app.current_variable = variable
        self.app.variable_name_edit.setText(new_name)
        self.app.variable_type_combo.setCurrentText(variable.type)
        self.app.variable_prompt_edit.setText(variable.prompt_text)
        self.app.variable_default_edit.setText(variable.default_value)
        self.app.variable_options_text.setPlainText("\n".join(variable.list_options))
        self.app.variable_notes_text.setPlainText(variable.notes)
        self.app.apply_variable()

    def rename_template(self, new_name: str) -> None:
        template = self.app.store.templates[0]
        self.app.current_template = template
        self.app.template_name_edit.setText(new_name)
        self.app.template_description_edit.setText(template.description)
        self.app.template_body_text.setPlainText(template.body)
        self.app.template_notes_text.setPlainText(template.notes)
        self.app.apply_template()


class RenameCascadeTests(_Window, unittest.TestCase):
    def test_confirming_repoints_every_reference(self) -> None:
        with mock.patch.object(app_module, "confirm", return_value=True):
            self.rename_variable("client")

        self.assertEqual(self.app.store.variables[0].name, "client")
        self.assertEqual(self.app.store.expansions[0].replacement, "Dear {VAR:client},")
        self.assertEqual(self.app.store.templates[0].body, "Regards, {VAR:client}")

    def test_the_renamed_library_still_generates(self) -> None:
        # The point of the whole change: the state left behind must be one the
        # generator accepts.
        with mock.patch.object(app_module, "confirm", return_value=True):
            self.rename_variable("client")

        render_ahk(self.app.store)  # would raise on a dangling reference

    def test_declining_abandons_the_rename_entirely(self) -> None:
        # Half-applying it -- renaming the definition but not the uses -- is
        # the bug, so No has to leave the definition alone too.
        with mock.patch.object(app_module, "confirm", return_value=False):
            self.rename_variable("client")

        self.assertEqual(self.app.store.variables[0].name, "client_name")
        self.assertEqual(self.app.store.expansions[0].replacement, "Dear {VAR:client_name},")

    def test_an_unreferenced_rename_is_not_questioned(self) -> None:
        self.app.store.expansions.clear()
        self.app.store.templates.clear()

        with mock.patch.object(app_module, "confirm") as asked:
            self.rename_variable("client")

        self.assertFalse(asked.called)
        self.assertEqual(self.app.store.variables[0].name, "client")

    def test_editing_a_variable_without_renaming_is_not_questioned(self) -> None:
        variable = self.app.store.variables[0]
        self.app.current_variable = variable
        self.app.variable_name_edit.setText(variable.name)
        self.app.variable_type_combo.setCurrentText(variable.type)
        self.app.variable_prompt_edit.setText("A new prompt")
        self.app.variable_default_edit.setText("")
        self.app.variable_options_text.setPlainText("")
        self.app.variable_notes_text.setPlainText("")

        with mock.patch.object(app_module, "confirm") as asked:
            self.app.apply_variable()

        self.assertFalse(asked.called)
        self.assertEqual(self.app.store.variables[0].prompt_text, "A new prompt")

    def test_renaming_a_template_repoints_its_users(self) -> None:
        with mock.patch.object(app_module, "confirm", return_value=True):
            self.rename_template("Sign Off")

        self.assertEqual(self.app.store.templates[0].name, "Sign Off")
        self.assertEqual(self.app.store.expansions[1].replacement, "{TPL:Sign Off}")
        render_ahk(self.app.store)

    def test_the_open_editor_is_repointed_too(self) -> None:
        # The expansion editor holds its own copy of the text, which may carry
        # edits not applied yet, so it is renamed in place rather than reloaded.
        self.app.replacement_text.setPlainText("Dear {VAR:client_name}, unsaved")

        with mock.patch.object(app_module, "confirm", return_value=True):
            self.rename_variable("client")

        self.assertEqual(self.app.replacement_text.toPlainText(), "Dear {VAR:client}, unsaved")


class TemplateNameTests(unittest.TestCase):
    """A name that is accepted must be one that can be referred to.

    A reference is written {TPL:name} and the placeholder pattern reads the
    name as everything up to the next brace, so a name holding one cannot
    round-trip. validate_template only rejected blank names, and the rename
    cascade would write the broken reference into every expansion that used
    the old name.
    """

    def test_a_name_holding_a_brace_is_rejected(self) -> None:
        for name in ("Bad}Name", "Bad{Name", "{Bad}", "}"):
            with self.subTest(name):
                with self.assertRaisesRegex(ValueError, "brace"):
                    validate_template(TemplateDef(name, body="x"))

    def test_names_that_only_look_awkward_are_accepted(self) -> None:
        # Each of these was checked against the parser and round-trips
        # exactly, so refusing them would gain nothing.
        for name in ("Client Follow-Up", "Bad|Name", "Bad:Name", 'Bad"Name',
                     "Bad;Name", "Signoff — EU", "50% Off"):
            with self.subTest(name):
                validate_template(TemplateDef(name, body="x"))

    def test_every_accepted_name_survives_a_cascade_rename(self) -> None:
        # The property the rule exists for: rename a referenced template to
        # each accepted name and the library must still generate.
        for name in ("Renamed", "With Spaces", "Bad|Name", "Bad:Name",
                     'Bad"Name', "Bad;Name", "50% Off"):
            with self.subTest(name):
                store = ExpansionStore(
                    sections=["Work"],
                    expansions=[Expansion("Work", ";sig", "A {TPL:Old} B")],
                    templates=[TemplateDef("Old", body="body text")],
                )

                validate_template(TemplateDef(name, body="body text"))
                rename_references(store, "TPL", "Old", name)
                store.templates[0].name = name

                self.assertEqual(
                    store.expansions[0].replacement, "A {TPL:%s} B" % name
                )
                self.assertIn("A body text B", render_ahk(store))

    def test_a_brace_name_never_reaches_the_cascade(self) -> None:
        # apply_template validates before renaming, so the store is never left
        # holding a reference that cannot be parsed.
        with self.assertRaises(ValueError):
            validate_template(TemplateDef("Bad}Name", body="x"))


class DeleteBlockTests(_Window, unittest.TestCase):
    def _select(self, row: int, table_name: str) -> None:
        # The delete handlers read the selection through selectedRows, so the
        # row has to be selected rather than merely current.
        table = getattr(self.app, table_name)
        table.selectRow(row)

    def test_a_referenced_variable_cannot_be_deleted(self) -> None:
        self._select(0, "variable_tree")

        with mock.patch.object(app_module, "show_error") as reported:
            with mock.patch.object(app_module, "confirm") as asked:
                self.app.delete_variable()

        self.assertTrue(reported.called, "the refusal was silent")
        self.assertFalse(asked.called, "it asked before checking")
        self.assertEqual(len(self.app.store.variables), 1)

    def test_the_refusal_names_the_dependents(self) -> None:
        self._select(0, "variable_tree")

        with mock.patch.object(app_module, "show_error") as reported:
            self.app.delete_variable()

        message = reported.call_args[0][2]
        self.assertIn(';hi', message)
        self.assertIn("Signoff", message)

    def test_a_referenced_template_cannot_be_deleted(self) -> None:
        self._select(0, "template_tree")

        with mock.patch.object(app_module, "show_error") as reported:
            self.app.delete_template()

        self.assertTrue(reported.called)
        self.assertEqual(len(self.app.store.templates), 1)

    def test_an_unreferenced_variable_still_deletes(self) -> None:
        self.app.store.expansions.clear()
        self.app.store.templates.clear()
        self.app.refresh_variables()
        self._select(0, "variable_tree")

        with mock.patch.object(app_module, "confirm", return_value=True):
            self.app.delete_variable()

        self.assertEqual(self.app.store.variables, [])


if __name__ == "__main__":
    unittest.main()
