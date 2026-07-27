import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

# Run Qt without a real display so the GUI tests work headlessly (e.g. in CI).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

import app as app_module
from ahk_manager import (
    BACKUP_RETENTION_LIMIT,
    Expansion,
    ExpansionStore,
    backup_file,
    backup_timestamp,
    list_backups,
    restore_backup,
)
from app import ExpansionApp

# A single QApplication must exist for the lifetime of the process.
_qt_app = QApplication.instance() or QApplication([])


class BackupRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        self.dir = Path(self._temp.name)
        self.target = self.dir / "expansions.json"

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_lists_backups_newest_first(self) -> None:
        self.target.write_text("one", encoding="utf-8")
        first = backup_file(self.target)
        self.target.write_text("two", encoding="utf-8")
        second = backup_file(self.target)

        listed = list_backups(self.target)

        self.assertEqual(listed[0], second)
        self.assertEqual(listed[1], first)

    def test_lists_nothing_when_never_backed_up(self) -> None:
        self.target.write_text("one", encoding="utf-8")

        self.assertEqual(list_backups(self.target), [])

    def test_timestamp_is_read_back_out_of_the_filename(self) -> None:
        label = backup_timestamp(self.dir / "expansions.20260727_143005.bak.json")

        self.assertEqual(label, "2026-07-27 14:30:05")

    def test_timestamp_falls_back_to_the_name_when_unparseable(self) -> None:
        label = backup_timestamp(self.dir / "not-a-backup.json")

        self.assertEqual(label, "not-a-backup.json")

    def test_restoring_replaces_the_file(self) -> None:
        self.target.write_text("original", encoding="utf-8")
        backup = backup_file(self.target)
        assert backup is not None
        self.target.write_text("edited badly", encoding="utf-8")

        restore_backup(backup, self.target)

        self.assertEqual(self.target.read_text(encoding="utf-8"), "original")

    def test_restoring_backs_up_what_it_replaces(self) -> None:
        # A mis-click must not be the end of the current data.
        self.target.write_text("original", encoding="utf-8")
        backup = backup_file(self.target)
        assert backup is not None
        self.target.write_text("work I actually wanted", encoding="utf-8")

        safety_copy = restore_backup(backup, self.target)

        self.assertIsNotNone(safety_copy)
        assert safety_copy is not None
        self.assertEqual(
            safety_copy.read_text(encoding="utf-8"), "work I actually wanted"
        )

    def test_restoring_a_vanished_backup_is_refused(self) -> None:
        self.target.write_text("original", encoding="utf-8")

        with self.assertRaises(ValueError):
            restore_backup(self.dir / "gone.20260101_000000.bak.json", self.target)

    def test_only_the_newest_backups_are_kept(self) -> None:
        self.target.write_text("v0", encoding="utf-8")
        for index in range(BACKUP_RETENTION_LIMIT + 3):
            self.target.write_text(f"v{index}", encoding="utf-8")
            backup_file(self.target)

        self.assertEqual(len(list_backups(self.target)), BACKUP_RETENTION_LIMIT)


class HelpPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        self.json_path = Path(self._temp.name) / "expansions.json"
        ExpansionStore(
            sections=["Work"], expansions=[Expansion("Work", ";seed", "seed")]
        ).save(self.json_path)
        self._original_path = app_module.JSON_PATH
        app_module.JSON_PATH = self.json_path
        self.app = ExpansionApp()

    def tearDown(self) -> None:
        self.app.close()
        app_module.JSON_PATH = self._original_path
        self._temp.cleanup()

    def test_help_is_the_last_nav_entry(self) -> None:
        labels = [self.app.nav.item(i).text() for i in range(self.app.nav.count())]

        self.assertIn("Help", labels[-1])
        self.assertEqual(self.app.stack.count(), len(labels))

    def test_selecting_help_shows_its_page(self) -> None:
        help_index = self.app.nav.count() - 1

        self.app.nav.setCurrentRow(help_index)

        self.assertEqual(self.app.stack.currentIndex(), help_index)

    def test_help_page_offers_both_restore_buttons(self) -> None:
        labels = {button.text() for button in self.app.findChildren(QPushButton)}

        self.assertTrue(
            any("Restore" in label and "expansions" in label for label in labels),
            f"no expansions restore button in {sorted(labels)}",
        )
        self.assertTrue(
            any("Restore" in label and "AHK" in label for label in labels),
            f"no AHK restore button in {sorted(labels)}",
        )

    def test_help_text_covers_the_placeholders_it_documents(self) -> None:
        # The help is the only place the placeholder syntax is explained.
        for token in (
            "AHK_INPUT",
            "AHK_SELECT",
            "AHK_EXPR",
            "AHK_KEY",
            "AHK_IMAGE",
            "VAR:name",
            "TPL:name",
        ):
            self.assertIn(token, app_module.HELP_HTML)

    def test_restoring_reloads_the_ui_from_the_restored_file(self) -> None:
        # The backup is taken on the session's first edit, so it holds the
        # pre-edit state; restoring it must undo the edit both on disk and on
        # screen. A stale in-memory store would write the edit straight back.
        self.app.current_expansion = None
        self.app.section_combo.setCurrentText("Work")
        self.app.trigger_edit.setText(";regrettable")
        self.app.replacement_text.setPlainText("oops")
        self.app.apply_form()
        self.assertIn(
            ";regrettable", [e.trigger for e in self.app.store.expansions]
        )

        def stub_get_item(
            parent, title: str, label: str, items: list[str], *args, **kwargs
        ) -> tuple[str, bool]:
            """Pick the newest backup, which is the one the dialog preselects."""
            return items[0], True

        stub_dialog = type(
            "StubDialog", (), {"getItem": staticmethod(stub_get_item)}
        )
        original = (
            app_module.QInputDialog,
            app_module.confirm,
            app_module.show_info,
        )
        app_module.QInputDialog = stub_dialog
        app_module.confirm = lambda *args, **kwargs: True
        app_module.show_info = lambda *args, **kwargs: None
        try:
            self.app.restore_json_backup()
        finally:
            (
                app_module.QInputDialog,
                app_module.confirm,
                app_module.show_info,
            ) = original

        triggers = [e.trigger for e in self.app.store.expansions]
        self.assertNotIn(";regrettable", triggers)
        self.assertIn(";seed", triggers)
        self.assertNotIn(";regrettable", self.json_path.read_text(encoding="utf-8"))

    def test_help_sets_no_colours_so_both_themes_stay_readable(self) -> None:
        lowered = app_module.HELP_HTML.lower()

        self.assertNotIn("color:", lowered)
        self.assertNotIn("bgcolor", lowered)


if __name__ == "__main__":
    unittest.main()
