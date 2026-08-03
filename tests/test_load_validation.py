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

    def test_a_missing_file_is_not_an_error(self) -> None:
        self.assertEqual(ExpansionStore.load(self.dir / "absent.json").expansions, [])

    def test_the_guard_does_not_reject_a_real_store(self) -> None:
        ExpansionStore(
            sections=["Work"], expansions=[Expansion("Work", ";a", "first")]
        ).save(self.path)

        loaded = ExpansionStore.load(self.path)

        self.assertEqual(loaded.sections, ["Work"])
        self.assertEqual(loaded.expansions[0].trigger, ";a")


class StartupRecoveryTests(unittest.TestCase):
    """A clobbered store must not stop the window opening.

    The Help page restores a backup, which is the fix for a bad
    expansions.json -- and it is unreachable if the window never opens. The exe
    is built windowed, so an exception here surfaces as a crash box rather than
    anything the user can act on.
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

    def test_the_window_opens_on_a_store_that_is_not_an_object(self) -> None:
        self.json_path.write_text("null", encoding="utf-8")

        with mock.patch.object(app_module, "show_error") as reported:
            window = ExpansionApp()
        self.addCleanup(window.close)

        self.assertTrue(reported.called, "the user was told nothing")
        self.assertEqual(window.store.expansions, [])

    def test_the_window_opens_on_a_collection_of_the_wrong_shape(self) -> None:
        # _load_store catches ValueError only, so a collection that raised
        # TypeError took the window down with it before it could be reported.
        for label, content in (
            ("null sections", '{"sections": null}'),
            ("numeric templates", '{"templates": 7}'),
            ("a non-object expansion", '{"expansions": [1]}'),
        ):
            with self.subTest(label):
                self.json_path.write_text(content, encoding="utf-8")

                with mock.patch.object(app_module, "show_error") as reported:
                    window = ExpansionApp()
                self.addCleanup(window.close)

                self.assertTrue(reported.called, "the user was told nothing")
                self.assertEqual(window.store.expansions, [])


if __name__ == "__main__":
    unittest.main()
