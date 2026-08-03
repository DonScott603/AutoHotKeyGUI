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
from ahk_manager import AppSettings, Expansion, ExpansionStore
from app import ExpansionApp

# A single QApplication must exist for the lifetime of the process.
_qt_app = QApplication.instance() or QApplication([])

# Valid JSON that is not an object. Each of these parses cleanly and then hits
# data.get, so each used to raise AttributeError rather than a load error.
NON_OBJECT_JSON = [
    ("an array", "[]"),
    ("a string", '"hello"'),
    ("a number", "42"),
    ("null", "null"),
]


class LoadValidationTests(unittest.TestCase):
    """A file that parses but holds the wrong shape must be a load error.

    Both loaders read straight off json.load with data.get. Malformed JSON was
    already refused with ValueError, but well-formed JSON that is not an object
    slipped through as AttributeError, which no caller catches.
    """

    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        self.dir = Path(self._temp.name)
        self.path = self.dir / "expansions.json"

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_store_refuses_valid_json_that_is_not_an_object(self) -> None:
        for label, content in NON_OBJECT_JSON:
            with self.subTest(label):
                self.path.write_text(content, encoding="utf-8")

                with self.assertRaises(ValueError):
                    ExpansionStore.load(self.path)

    def test_settings_refuses_valid_json_that_is_not_an_object(self) -> None:
        for label, content in NON_OBJECT_JSON:
            with self.subTest(label):
                self.path.write_text(content, encoding="utf-8")

                with self.assertRaises(ValueError):
                    AppSettings.load(self.path, Path("unused"))

    def test_the_message_names_the_file_and_what_was_found(self) -> None:
        # It goes straight into a dialog, so it has to say which file and why.
        self.path.write_text("[]", encoding="utf-8")

        with self.assertRaises(ValueError) as caught:
            ExpansionStore.load(self.path)

        self.assertIn("expansions.json", str(caught.exception))
        self.assertIn("list", str(caught.exception))

    def test_malformed_json_is_still_a_load_error(self) -> None:
        # Pre-existing behaviour, kept honest now that a second guard sits
        # beside it.
        self.path.write_text('{"sections":', encoding="utf-8")

        with self.assertRaises(ValueError):
            ExpansionStore.load(self.path)

    def test_a_collection_that_is_not_an_array_is_a_load_error(self) -> None:
        # null and a number reach a for loop and raise TypeError, which no
        # caller catches; an object and a string iterate quietly and lose the
        # file's contents on the next autosave. All four are the wrong shape.
        for key in ("sections", "expansions", "variables", "templates"):
            for label, value in (("null", "null"), ("a number", "4"),
                                 ("an object", "{}"), ("a string", '"abc"')):
                with self.subTest(f"{key} is {label}"):
                    self.path.write_text(f'{{"{key}": {value}}}', encoding="utf-8")

                    with self.assertRaises(ValueError):
                        ExpansionStore.load(self.path)

    def test_the_collection_message_names_the_field_and_the_shape(self) -> None:
        self.path.write_text('{"expansions": {}}', encoding="utf-8")

        with self.assertRaises(ValueError) as caught:
            ExpansionStore.load(self.path)

        self.assertIn("expansions.json", str(caught.exception))
        self.assertIn('"expansions"', str(caught.exception))
        self.assertIn("dict", str(caught.exception))

    def test_a_record_entry_that_is_not_an_object_is_a_load_error(self) -> None:
        # Refused rather than skipped: a skipped entry is gone for good once
        # autosave rewrites the file in the normalised schema.
        for key in ("expansions", "variables", "templates"):
            with self.subTest(key):
                self.path.write_text(f'{{"{key}": [1]}}', encoding="utf-8")

                with self.assertRaises(ValueError):
                    ExpansionStore.load(self.path)

    def test_the_entry_message_locates_the_bad_entry(self) -> None:
        self.path.write_text('{"expansions": [{"trigger": ";a"}, "oops"]}', encoding="utf-8")

        with self.assertRaises(ValueError) as caught:
            ExpansionStore.load(self.path)

        self.assertIn("entry 2", str(caught.exception))

    def test_a_section_name_that_is_not_a_string_is_a_load_error(self) -> None:
        # str() would turn 4 into "4" and an object into its Python repr, both
        # of which look like real section names further in.
        self.path.write_text('{"sections": ["Work", 4]}', encoding="utf-8")

        with self.assertRaises(ValueError) as caught:
            ExpansionStore.load(self.path)

        self.assertIn("entry 2", str(caught.exception))

    def test_blank_section_names_are_still_dropped(self) -> None:
        # Normalisation, not corruption: the stricter check must not turn this
        # into a load error.
        self.path.write_text('{"sections": ["Work", "  ", ""]}', encoding="utf-8")

        self.assertEqual(ExpansionStore.load(self.path).sections, ["Work"])

    def test_absent_collections_are_not_an_error(self) -> None:
        self.path.write_text('{"sections": ["Work"]}', encoding="utf-8")

        loaded = ExpansionStore.load(self.path)

        self.assertEqual(loaded.sections, ["Work"])
        self.assertEqual(loaded.expansions, [])

    def _write(self, payload: dict[str, object]) -> None:
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def test_enabled_must_be_a_real_boolean(self) -> None:
        # The worst of these: bool("false") is True and bool(None) is False, so
        # either one silently flips an expansion and the next autosave writes
        # the flipped value back as the truth.
        for label, value in (("a string", "false"), ("null", None),
                             ("a number", 1), ("an array", [])):
            with self.subTest(label):
                self._write({"expansions": [{"trigger": ";a", "enabled": value}]})

                with self.assertRaises(ValueError):
                    ExpansionStore.load(self.path)

    def test_a_textual_field_must_be_a_string(self) -> None:
        # str() accepts anything and produces a Python repr that looks like
        # content: {"a": 1} becomes the six characters "{'a': 1}".
        for field, value in (("trigger", {"a": 1}), ("replacement", ["x", "y"]),
                             ("section", 4), ("notes", True)):
            with self.subTest(field):
                self._write({"expansions": [{field: value}]})

                with self.assertRaises(ValueError):
                    ExpansionStore.load(self.path)

    def test_list_options_must_hold_only_strings(self) -> None:
        self._write({
            "variables": [
                {"name": "v", "type": "list_selection", "list_options": ["a", {"k": 1}]}
            ]
        })

        with self.assertRaises(ValueError) as caught:
            ExpansionStore.load(self.path)

        # The field is the right type and only one of its entries is not, so
        # the message has to say which.
        self.assertIn("entry 2", str(caught.exception))

    def test_the_field_message_locates_the_field(self) -> None:
        self._write({
            "expansions": [{"trigger": ";a"}, {"trigger": ";b", "enabled": "no"}]
        })

        with self.assertRaises(ValueError) as caught:
            ExpansionStore.load(self.path)

        message = str(caught.exception)
        self.assertIn("expansions.json", message)
        self.assertIn('"expansions"', message)
        self.assertIn("entry 2", message)
        self.assertIn('"enabled"', message)
        self.assertIn("str", message)

    def test_null_is_still_accepted_for_a_textual_field(self) -> None:
        # from_dict has always read null as "not set", and a file written that
        # way is not corrupt.
        self._write({"expansions": [{"trigger": ";a", "replacement": "x", "notes": None}]})

        self.assertEqual(ExpansionStore.load(self.path).expansions[0].notes, "")

    def test_the_newline_separated_option_form_still_loads(self) -> None:
        self._write({
            "variables": [
                {"name": "v", "type": "list_selection", "list_options": "a\nb"}
            ]
        })

        self.assertEqual(ExpansionStore.load(self.path).variables[0].list_options, ["a", "b"])

    def test_an_unknown_field_is_left_alone(self) -> None:
        # A file written by a later version has to keep loading here.
        self._write({"expansions": [{"trigger": ";a", "replacement": "x", "later": {"z": 1}}]})

        self.assertEqual(ExpansionStore.load(self.path).expansions[0].trigger, ";a")

    def test_a_missing_file_is_not_an_error(self) -> None:
        self.assertEqual(ExpansionStore.load(self.dir / "absent.json").expansions, [])

    def test_the_guard_does_not_reject_a_real_store(self) -> None:
        ExpansionStore(
            sections=["Work"], expansions=[Expansion("Work", ";a", "first")]
        ).save(self.path)

        loaded = ExpansionStore.load(self.path)

        self.assertEqual(loaded.sections, ["Work"])
        self.assertEqual(loaded.expansions[0].trigger, ";a")


def close_window(window: ExpansionApp) -> None:
    """Close a test window without tripping the unsaved-changes prompt.

    closeEvent asks before discarding a refused write, which is right in the
    application and fatal here: the dialog is modal, so a headless run blocks
    on it forever rather than failing. Tests that care about the prompt drive
    it explicitly; everything else closes through this.
    """
    window._set_unsaved(False)
    window.close()


class _RedirectedPaths:
    """Point every path the window touches at a temporary folder.

    A mixin rather than a base test case: inheriting one test class from
    another would re-run its tests under the subclass's name.
    """

    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        root = Path(self._temp.name)
        self.json_path = root / "expansions.json"
        # As in test_autosave: every path the window reads or writes has to be
        # redirected, or the test edits the real install.
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


class StartupRecoveryTests(_RedirectedPaths, unittest.TestCase):
    """A clobbered store must not stop the window opening.

    The Help page restores a backup, which is the fix for a bad
    expansions.json -- and it is unreachable if the window never opens. The exe
    is built windowed, so an exception here surfaces as a crash box rather than
    anything the user can act on.
    """

    def test_the_window_opens_on_a_store_that_is_not_an_object(self) -> None:
        self.json_path.write_text("null", encoding="utf-8")

        with mock.patch.object(app_module, "show_error") as reported:
            window = ExpansionApp()
        self.addCleanup(close_window, window)

        self.assertTrue(reported.called, "the user was told nothing")
        self.assertEqual(window.store.expansions, [])

    def test_the_window_opens_on_a_collection_of_the_wrong_shape(self) -> None:
        # _load_store catches ValueError only, so a collection that raised
        # TypeError took the window down with it before it could be reported.
        for label, content in (
            ("null sections", '{"sections": null}'),
            ("numeric templates", '{"templates": 7}'),
            ("a non-object expansion", '{"expansions": [1]}'),
            ("a bad field type", '{"expansions": [{"enabled": "false"}]}'),
        ):
            with self.subTest(label):
                self.json_path.write_text(content, encoding="utf-8")

                with mock.patch.object(app_module, "show_error") as reported:
                    window = ExpansionApp()
                self.addCleanup(close_window, window)

                self.assertTrue(reported.called, "the user was told nothing")
                self.assertEqual(window.store.expansions, [])


# Malformed JSON that plainly still holds a library: the point of refusing to
# overwrite it is that a human, or a later version, could get this back.
RECOVERABLE = '{"expansions": [{"trigger": ";a", "replacement": "important"}]'


class UnreadableStoreWriteTests(_RedirectedPaths, unittest.TestCase):
    """A store that failed to load must not be overwritten unasked.

    Autosave writes on every edit, so the empty store standing in for an
    unreadable file would replace it at the first keystroke. _backup_once
    copies the file aside, but only once a session and into a folder that
    rotates, so that alone is not enough.
    """

    def _window(self) -> ExpansionApp:
        self.json_path.write_text(RECOVERABLE, encoding="utf-8")
        with mock.patch.object(app_module, "show_error"):
            window = ExpansionApp()
        self.addCleanup(close_window, window)
        return window

    def test_declining_leaves_the_file_untouched(self) -> None:
        window = self._window()

        with mock.patch.object(app_module, "confirm", return_value=False) as asked:
            saved = window.persist()

        self.assertFalse(saved)
        self.assertTrue(asked.called, "the file was replaced without asking")
        self.assertEqual(self.json_path.read_text(encoding="utf-8"), RECOVERABLE)

    def test_confirming_writes_and_backs_the_original_up(self) -> None:
        window = self._window()

        with mock.patch.object(app_module, "confirm", return_value=True):
            self.assertTrue(window.persist())

        self.assertNotEqual(self.json_path.read_text(encoding="utf-8"), RECOVERABLE)
        backups = list((Path(self._temp.name) / "backups").glob("*"))
        self.assertTrue(
            any(b.read_text(encoding="utf-8") == RECOVERABLE for b in backups),
            f"the unreadable file was not kept: {[b.name for b in backups]}",
        )

    def test_the_question_is_asked_once(self) -> None:
        # Every edit autosaves, so a prompt that repeated would be unusable.
        window = self._window()

        with mock.patch.object(app_module, "confirm", return_value=True) as asked:
            window.persist()
            window.persist()

        self.assertEqual(asked.call_count, 1)

    def test_generate_and_run_does_not_bypass_the_question(self) -> None:
        # This path called store.save directly, so it wrote over the file
        # without the backup persist takes.
        window = self._window()

        with mock.patch.object(app_module, "confirm", return_value=False) as asked:
            with mock.patch.object(app_module, "generate_ahk") as generated:
                window.generate_and_run_ahk()

        self.assertTrue(asked.called)
        self.assertFalse(generated.called, "the script was written anyway")
        self.assertEqual(self.json_path.read_text(encoding="utf-8"), RECOVERABLE)

    def test_a_store_that_loaded_is_never_questioned(self) -> None:
        ExpansionStore(
            sections=["Work"], expansions=[Expansion("Work", ";a", "first")]
        ).save(self.json_path)
        window = ExpansionApp()
        self.addCleanup(close_window, window)

        with mock.patch.object(app_module, "confirm") as asked:
            self.assertTrue(window.persist())

        self.assertFalse(asked.called)


class FailedSaveReportingTests(_RedirectedPaths, unittest.TestCase):
    """A write that did not happen must not be reported as one.

    persist has always returned False on failure, but every handler ignored it
    and set its own success status afterwards -- so a refused write ended with
    'Saved variable "x".' on screen while the file was untouched and the edit
    existed only in memory, ready to be lost on close.
    """

    def _window(self) -> ExpansionApp:
        self.json_path.write_text(RECOVERABLE, encoding="utf-8")
        with mock.patch.object(app_module, "show_error"):
            window = ExpansionApp()
        self.addCleanup(close_window, window)
        return window

    def _add_variable(self, window: ExpansionApp) -> None:
        window.current_variable = None
        window.variable_name_edit.setText("client")
        window.variable_type_combo.setCurrentText("text_input")
        window.variable_prompt_edit.setText("Client")
        window.apply_variable()

    def test_a_refused_save_is_not_reported_as_saved(self) -> None:
        window = self._window()

        with mock.patch.object(app_module, "confirm", return_value=False):
            self._add_variable(window)

        self.assertNotIn("Saved variable", window.status_label.text())
        self.assertIn("nothing was saved", window.status_label.text())

    def test_a_refused_save_leaves_the_unsaved_marker_showing(self) -> None:
        # The status line is transient; this is what has to survive until the
        # write actually happens.
        window = self._window()

        with mock.patch.object(app_module, "confirm", return_value=False):
            self._add_variable(window)

        self.assertTrue(window._unsaved_changes)
        self.assertEqual(window.unsaved_label.text(), "Unsaved changes")

    def test_a_successful_save_reports_and_clears_the_marker(self) -> None:
        window = self._window()

        with mock.patch.object(app_module, "confirm", return_value=True):
            self._add_variable(window)

        self.assertIn("Saved variable", window.status_label.text())
        self.assertFalse(window._unsaved_changes)
        self.assertEqual(window.unsaved_label.text(), "")

    def test_the_marker_survives_a_later_unrelated_status(self) -> None:
        window = self._window()

        with mock.patch.object(app_module, "confirm", return_value=False):
            self._add_variable(window)
        window.set_status("something else happened")

        self.assertEqual(window.unsaved_label.text(), "Unsaved changes")

    def test_closing_with_unsaved_changes_asks_first(self) -> None:
        window = self._window()
        with mock.patch.object(app_module, "confirm", return_value=False):
            self._add_variable(window)

        with mock.patch.object(app_module, "confirm", return_value=False) as asked:
            closed = window.close()

        self.assertTrue(asked.called, "the edit would have been lost silently")
        self.assertFalse(closed, "the window closed despite the refusal")

    def test_closing_proceeds_when_confirmed(self) -> None:
        window = self._window()
        with mock.patch.object(app_module, "confirm", return_value=False):
            self._add_variable(window)

        with mock.patch.object(app_module, "confirm", return_value=True):
            self.assertTrue(window.close())

    def test_closing_a_saved_window_does_not_ask(self) -> None:
        # Autosave means closing is normally free, and it has to stay free.
        window = self._window()
        with mock.patch.object(app_module, "confirm", return_value=True):
            self._add_variable(window)

        with mock.patch.object(app_module, "confirm") as asked:
            self.assertTrue(window.close())

        self.assertFalse(asked.called)

    def test_restoring_a_backup_clears_the_marker(self) -> None:
        # The store is reloaded from disk, so the window and the file agree
        # again whatever the refused write left behind.
        window = self._window()
        with mock.patch.object(app_module, "confirm", return_value=False):
            self._add_variable(window)

        # Stand in for the restore having put a readable file back, so the
        # reload inside restore_json_backup succeeds as it would in practice.
        ExpansionStore(
            sections=["Work"], expansions=[Expansion("Work", ";a", "x")]
        ).save(self.json_path)
        with mock.patch.object(
            ExpansionApp, "_restore_from_backup", return_value=self.json_path
        ):
            window.restore_json_backup()

        self.assertFalse(window._unsaved_changes)
        self.assertEqual(window.unsaved_label.text(), "")


if __name__ == "__main__":
    unittest.main()
