"""An import is applied only if the result can still generate.

Whether a merge produces a usable library depends on both sides and on the
conflict action, so it cannot be settled while parsing the imported file: a
reference the file leaves open may be one the importing library supplies, and
two templates that are each fine alone can close a cycle once merged. Leaving
it to generate time was not an answer either, because the merge wrote straight
into the live store and the result was autosaved.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

# Run Qt without a real display so the GUI tests work headlessly (e.g. in CI).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog

import app as app_module
from ahk_manager import (
    Expansion,
    ExpansionStore,
    TemplateDef,
    VariableDef,
    render_ahk,
)
from app import ExpansionApp
from qt_cleanup import destroy_all_windows

# A single QApplication must exist for the lifetime of the process.
_qt_app = QApplication.instance() or QApplication([])


class ImportCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        destroy_all_windows()
        self._temp = TemporaryDirectory()
        self.root = Path(self._temp.name)
        self._saved_paths = (
            app_module.JSON_PATH,
            app_module.SETTINGS_PATH,
            app_module.AHK_PATH,
            app_module.DEFAULT_BACKUP_DIR,
            app_module.UI_PREFS_PATH,
        )
        app_module.JSON_PATH = self.root / "expansions.json"
        app_module.SETTINGS_PATH = self.root / "settings.json"
        app_module.AHK_PATH = self.root / "text_expansions.ahk"
        app_module.DEFAULT_BACKUP_DIR = self.root / "backups"
        app_module.UI_PREFS_PATH = self.root / "ui_prefs.json"

    def tearDown(self) -> None:
        (
            app_module.JSON_PATH,
            app_module.SETTINGS_PATH,
            app_module.AHK_PATH,
            app_module.DEFAULT_BACKUP_DIR,
            app_module.UI_PREFS_PATH,
        ) = self._saved_paths
        self._temp.cleanup()

    def _window(self, store: ExpansionStore) -> ExpansionApp:
        store.save(app_module.JSON_PATH)
        window = ExpansionApp()
        self.addCleanup(self._close, window)
        return window

    @staticmethod
    def _close(window: ExpansionApp) -> None:
        # closeEvent asks about unsaved changes, and that dialog is modal, so a
        # headless run would block on it rather than fail.
        window._set_unsaved(False)
        window.close()
        window.deleteLater()
        _qt_app.processEvents()

    def _import(self, window: ExpansionApp, body: str, action: str = "skip") -> str:
        """Drive the import handler over a file holding body. Returns the error."""
        source = self.root / "in.ahk"
        source.write_text(
            "#Requires AutoHotkey v2.0\n\n" + body + "\n", encoding="utf-8"
        )
        reported: dict[str, str] = {}
        with mock.patch.object(
            app_module, "show_error", lambda parent, title, message: reported.update(m=message)
        ):
            with mock.patch.object(
                QFileDialog, "getOpenFileName", return_value=(str(source), "")
            ):
                with mock.patch.object(app_module, "ImportConflictDialog") as dialog:
                    dialog.return_value.exec.return_value = True
                    dialog.return_value.choice = action
                    window.import_ahk()
        return reported.get("m", "")

    def test_an_unresolved_reference_is_refused(self) -> None:
        window = self._window(ExpansionStore(sections=["Work"]))

        error = self._import(window, '; @tem: {"replacement":"{VAR:missing}"}\n:C:;x::')

        self.assertIn("was not imported", error)
        self.assertIn('Undefined variable "missing"', error)
        self.assertEqual(window.store.expansions, [])

    def test_a_refused_import_changes_nothing_on_disk(self) -> None:
        window = self._window(
            ExpansionStore(
                sections=["Work"], expansions=[Expansion("Work", ";keep", "text")]
            )
        )
        before = app_module.JSON_PATH.read_text(encoding="utf-8")

        self._import(window, '; @tem: {"replacement":"{VAR:missing}"}\n:C:;x::')

        self.assertEqual(app_module.JSON_PATH.read_text(encoding="utf-8"), before)
        self.assertEqual([e.trigger for e in window.store.expansions], [";keep"])

    def test_a_reference_this_library_supplies_is_imported(self) -> None:
        # The reason the check cannot happen while parsing the file.
        window = self._window(
            ExpansionStore(
                sections=["Work"],
                variables=[VariableDef("known", "text_input", "P", "", [], "")],
            )
        )

        error = self._import(window, '; @tem: {"replacement":"{VAR:known}"}\n:C:;x::')

        self.assertEqual(error, "")
        self.assertEqual([e.trigger for e in window.store.expansions], [";x"])

    def test_a_cycle_the_merge_creates_is_refused(self) -> None:
        # Neither side is cyclic alone, so nothing short of checking the merged
        # result can see this.
        window = self._window(
            ExpansionStore(
                sections=["Work"],
                templates=[
                    TemplateDef("A", body="plain"),
                    TemplateDef("B", body="{TPL:A}"),
                ],
            )
        )

        error = self._import(
            window,
            '; @tem-template: {"name":"A","body":"{TPL:B}"}',
            action="overwrite",
        )

        self.assertIn("Circular template reference", error)
        unchanged = window.store.template_by_name("A")
        self.assertIsNotNone(unchanged)
        assert unchanged is not None
        self.assertEqual(unchanged.body, "plain")
        render_ahk(window.store)  # would raise if the cycle had landed

    def test_an_ordinary_import_still_applies(self) -> None:
        window = self._window(ExpansionStore(sections=["Work"]))

        error = self._import(window, ":CT:;btw::by the way")

        self.assertEqual(error, "")
        self.assertEqual([e.trigger for e in window.store.expansions], [";btw"])
        self.assertIn("Imported", window.status_label.text())

    def test_an_already_broken_library_does_not_switch_the_check_off(self) -> None:
        # Comparing "does either store have a problem" made these two stores
        # indistinguishable, so once the library had one fault of its own any
        # number of further broken records could be imported on top of it.
        window = self._window(
            ExpansionStore(
                sections=["Work"],
                expansions=[Expansion("Work", ";old", "{VAR:old_missing}")],
            )
        )

        error = self._import(
            window, '; @tem: {"replacement":"{VAR:new_missing}"}\n:C:;new::'
        )

        self.assertIn("new_missing", error)
        self.assertEqual([e.trigger for e in window.store.expansions], [";old"])

    def test_breaking_an_already_broken_record_differently_is_refused(self) -> None:
        # The record was already failing, so its key is not new -- but it fails
        # for a different reason now, which the import caused.
        window = self._window(
            ExpansionStore(
                sections=["Work"],
                expansions=[Expansion("Work", ";a", "{VAR:one_missing}")],
            )
        )

        error = self._import(
            window,
            '; @tem: {"replacement":"{VAR:another_missing}"}\n:C:;a::',
            action="overwrite",
        )

        self.assertIn("another_missing", error)
        self.assertEqual(
            window.store.expansions[0].replacement, "{VAR:one_missing}"
        )

    def test_a_library_that_already_cannot_generate_does_not_block_importing(self) -> None:
        # The check refuses what the import breaks. A library that was already
        # broken is the user's to repair, and barring imports until they had
        # would be an obstacle, not a safeguard.
        window = self._window(
            ExpansionStore(
                sections=["Work"],
                expansions=[Expansion("Work", ";broken", "{VAR:never_defined}")],
            )
        )

        error = self._import(window, ":CT:;btw::by the way")

        self.assertEqual(error, "")
        self.assertIn(";btw", [e.trigger for e in window.store.expansions])


if __name__ == "__main__":
    unittest.main()
