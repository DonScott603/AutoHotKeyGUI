import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

# Run Qt without a real display so the GUI tests work headlessly (e.g. in CI).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

import app as app_module
from ahk_manager import Expansion, ExpansionStore, TemplateDef, VariableDef
from app import ExpansionApp

# A single QApplication must exist for the lifetime of the process.
_qt_app = QApplication.instance() or QApplication([])


class AutoSaveTests(unittest.TestCase):
    """Edits must reach disk as they are applied.

    The window reads and writes the module-level JSON_PATH, so each test
    redirects it at a temporary file -- otherwise these would overwrite the
    real expansions.json sitting beside the app.
    """

    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        root = Path(self._temp.name)
        self.json_path = root / "expansions.json"
        self.backup_dir = root / "backups"
        ExpansionStore(
            sections=["Work"],
            expansions=[Expansion("Work", ";seed", "seed text")],
            variables=[VariableDef("seed_var", "text_input", "Seed", "", [], "")],
            templates=[TemplateDef("Seed Template", "", "seed body", "")],
        ).save(self.json_path)
        # Every path the window reads or writes has to be redirected, not just
        # the store: it also loads settings, resolves a default backup folder,
        # and migrates stray backups next to the configured script. Leave any
        # of those pointing at the real install and the tests edit real data.
        self._saved_paths = (
            app_module.JSON_PATH,
            app_module.SETTINGS_PATH,
            app_module.AHK_PATH,
            app_module.DEFAULT_BACKUP_DIR,
        )
        app_module.JSON_PATH = self.json_path
        app_module.SETTINGS_PATH = root / "settings.json"
        app_module.AHK_PATH = root / "text_expansions.ahk"
        app_module.DEFAULT_BACKUP_DIR = self.backup_dir
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

    def saved(self) -> dict:
        return json.loads(self.json_path.read_text(encoding="utf-8"))

    def test_applying_an_expansion_writes_it_to_disk(self) -> None:
        self.app.current_expansion = None
        self.app.section_combo.setCurrentText("Work")
        self.app.trigger_edit.setText(";fresh")
        self.app.replacement_text.setPlainText("fresh text")
        self.app.notes_text.setPlainText("")

        self.app.apply_form()

        triggers = [item["trigger"] for item in self.saved()["expansions"]]
        self.assertIn(";fresh", triggers)

    def test_deleting_an_expansion_writes_through(self) -> None:
        # A deletion that only lived in memory would reappear on next launch.
        index = next(
            i for i, e in enumerate(self.app.store.expansions) if e.trigger == ";seed"
        )
        del self.app.store.expansions[index]
        self.app.persist()

        triggers = [item["trigger"] for item in self.saved()["expansions"]]
        self.assertNotIn(";seed", triggers)

    def test_applying_a_variable_writes_it_to_disk(self) -> None:
        self.app.current_variable = None
        self.app.variable_name_edit.setText("fresh_var")
        self.app.variable_type_combo.setCurrentText("text_input")
        self.app.variable_prompt_edit.setText("Prompt")

        self.app.apply_variable()

        names = [item["name"] for item in self.saved()["variables"]]
        self.assertIn("fresh_var", names)

    def test_adding_a_section_writes_it_to_disk(self) -> None:
        self.app.store.add_section("Added")
        self.app.persist()

        self.assertIn("Added", self.saved()["sections"])

    def test_toggling_enabled_writes_through(self) -> None:
        expansion = self.app.store.expansions[0]
        expansion.enabled = not expansion.enabled
        self.app.persist()

        self.assertEqual(self.saved()["expansions"][0]["enabled"], expansion.enabled)

    def backups(self) -> list[Path]:
        if not self.backup_dir.is_dir():
            return []
        return sorted(self.backup_dir.glob("expansions.*.bak.json"))

    def test_browsing_without_editing_writes_no_backup(self) -> None:
        # Backing up on every launch would rotate useful copies out of the
        # retention window for sessions that changed nothing.
        self.app.refresh_expansions()
        self.app.refresh_variables()

        self.assertEqual(self.backups(), [])

    def test_first_edit_backs_up_the_pre_session_state(self) -> None:
        # The backup must hold what was on disk *before* this session, which is
        # what someone restoring a bad edit is reaching for.
        original = self.json_path.read_text(encoding="utf-8")
        self.app.current_expansion = None
        self.app.section_combo.setCurrentText("Work")
        self.app.trigger_edit.setText(";fresh")
        self.app.replacement_text.setPlainText("fresh text")

        self.app.apply_form()

        backups = self.backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), original)
        self.assertIn(";fresh", self.json_path.read_text(encoding="utf-8"))

    def test_later_edits_in_the_same_session_add_no_further_backups(self) -> None:
        for trigger in (";one", ";two", ";three"):
            self.app.current_expansion = None
            self.app.section_combo.setCurrentText("Work")
            self.app.trigger_edit.setText(trigger)
            self.app.replacement_text.setPlainText("text")
            self.app.apply_form()

        self.assertEqual(len(self.backups()), 1)

    def test_a_failing_backup_does_not_block_the_save(self) -> None:
        # Losing the safety net is bad; losing the edit as well would be worse.
        def explode(_path: Path, _backup_dir: Path | None = None) -> Path | None:
            raise OSError("backup target unavailable")

        original_backup = app_module.backup_file
        original_warn = app_module.show_warning
        warnings: list[str] = []
        app_module.backup_file = explode
        app_module.show_warning = lambda *args, **kwargs: warnings.append(args[-1])
        try:
            self.app.store.add_section("Added")
            self.app.persist()
        finally:
            app_module.backup_file = original_backup
            app_module.show_warning = original_warn

        self.assertEqual(len(warnings), 1)
        self.assertIn("Added", self.saved()["sections"])

    def test_footer_has_no_separate_save_button(self) -> None:
        # Saving is automatic, so a save button would be a no-op users could
        # still feel obliged to press.
        labels = {button.text() for button in self.app.findChildren(QPushButton)}
        self.assertNotIn("Save JSON", labels)
        self.assertTrue(
            any("Generate" in label and "Run AHK" in label for label in labels),
            f"expected a Generate & Run AHK button, got {sorted(labels)}",
        )


if __name__ == "__main__":
    unittest.main()
