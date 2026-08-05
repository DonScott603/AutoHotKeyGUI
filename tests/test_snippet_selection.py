"""The Snippets list drives the editor, and acts on whole selections.

Selecting a row used to load it into the editor immediately, which cannot
coexist with building a multi-row selection: every Ctrl-click would overwrite
the form. Editing is now an explicit Edit / double-click, and Delete and Toggle
On/Off read every selected row rather than only the first.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

# Run Qt without a real display so the GUI tests work headlessly (e.g. in CI).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTableWidgetSelectionRange

import app as app_module
from ahk_manager import Expansion, ExpansionStore
from app import ExpansionApp
from qt_cleanup import destroy_all_windows

# A single QApplication must exist for the lifetime of the process.
_qt_app = QApplication.instance() or QApplication([])


class SnippetSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        destroy_all_windows()
        self._temp = TemporaryDirectory()
        root = Path(self._temp.name)
        ExpansionStore(
            sections=["Work"],
            expansions=[
                Expansion("Work", ";one", "first"),
                Expansion("Work", ";two", "second"),
                Expansion("Work", ";three", "third"),
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

    def select_rows(self, *rows: int, focus: int | None = None) -> None:
        """Select rows the way a Ctrl-click does, adding to the selection.

        `focus` is the row wearing the focus rectangle -- Qt's current row,
        which a click leaves on the row last pointed at. Left out, no row is
        focused, as after a refresh rebuilds the table.
        """
        table = self.app.tree
        table.clearSelection()
        last_column = table.columnCount() - 1
        for row in rows:
            table.setRangeSelected(
                QTableWidgetSelectionRange(row, 0, row, last_column), True
            )
        if focus is not None:
            table.selectionModel().setCurrentIndex(
                table.model().index(focus, 0), QItemSelectionModel.SelectionFlag.NoUpdate
            )

    def triggers(self) -> list[str]:
        return [expansion.trigger for expansion in self.app.store.expansions]

    # -- opening the editor ------------------------------------------------
    def test_selecting_a_row_leaves_the_editor_alone(self) -> None:
        self.select_rows(1)

        self.assertEqual(self.app.trigger_edit.text(), "")
        self.assertIsNone(self.app.current_expansion)

    def test_edit_opens_the_selected_row(self) -> None:
        self.select_rows(1)

        self.app.load_selected_expansion()

        self.assertEqual(self.app.trigger_edit.text(), ";two")
        self.assertEqual(self.app.replacement_text.toPlainText(), "second")

    def test_edit_opens_the_focused_row_of_a_multi_row_selection(self) -> None:
        # Ctrl-clicking down the list leaves the focus rectangle on the last
        # row clicked; opening the topmost instead opened a row the user was
        # not pointing at.
        self.select_rows(0, 2, focus=2)

        self.app.load_selected_expansion()

        self.assertEqual(self.app.trigger_edit.text(), ";three")

    def test_edit_falls_back_to_the_topmost_when_no_row_is_focused(self) -> None:
        self.select_rows(2, 1)

        self.app.load_selected_expansion()

        self.assertEqual(self.app.trigger_edit.text(), ";two")

    def test_edit_ignores_a_focus_outside_the_selection(self) -> None:
        self.select_rows(0, 2, focus=1)

        self.app.load_selected_expansion()

        self.assertEqual(self.app.trigger_edit.text(), ";one")

    def test_double_clicking_opens_the_row_under_the_pointer(self) -> None:
        # A real double click, because the bug lived in Qt's press handling:
        # pressing a row that is already selected defers collapsing the
        # selection, so the double click arrives with every row still selected.
        self.app.resize(1180, 760)
        self.app.show()
        table = self.app.tree

        def centre(row: int):
            return table.visualRect(table.model().index(row, 1)).center()

        QTest.mouseClick(
            table.viewport(), Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier, centre(0),
        )
        QTest.mouseClick(
            table.viewport(), Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier, centre(2),
        )
        self.assertEqual(
            self.app.selected_expansion_indexes(), [0, 2], "the selection was not built"
        )

        QTest.mouseDClick(
            table.viewport(), Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier, centre(2),
        )

        self.assertEqual(self.app.trigger_edit.text(), ";three")
        self.assertIs(self.app.current_expansion, self.app.store.expansions[2])

    def test_a_double_click_opens_its_own_row_not_the_selection(self) -> None:
        # Isolates the double-click path from the selection: the handler is
        # told which row was clicked and must use it, rather than asking the
        # selection and hoping the two agree.
        self.select_rows(0, focus=0)

        self.app.tree.cellDoubleClicked.emit(2, 1)

        self.assertEqual(self.app.trigger_edit.text(), ";three")
        self.assertIs(self.app.current_expansion, self.app.store.expansions[2])

    def test_double_clicking_an_empty_row_opens_nothing(self) -> None:
        with mock.patch.object(app_module, "show_info") as reported:
            self.app.load_double_clicked_expansion(99)

        self.assertEqual(self.app.trigger_edit.text(), "")
        self.assertFalse(reported.called, "a stray double click should stay quiet")

    def test_edit_with_nothing_selected_says_so(self) -> None:
        self.app.tree.clearSelection()

        with mock.patch.object(app_module, "show_info") as reported:
            self.app.load_selected_expansion()

        self.assertTrue(reported.called, "Edit did nothing and said nothing")

    def test_applying_returns_the_editor_to_blank(self) -> None:
        self.select_rows(0)
        self.app.load_selected_expansion()
        self.app.replacement_text.setPlainText("edited")

        self.app.apply_form()

        self.assertEqual(self.app.store.expansions[0].replacement, "edited")
        self.assertEqual(self.app.trigger_edit.text(), "")
        self.assertEqual(self.app.replacement_text.toPlainText(), "")
        self.assertEqual(self.app.notes_text.toPlainText(), "")
        self.assertIsNone(self.app.current_expansion)
        # Everything but the section, which stays on what was just applied to.
        self.assertEqual(self.app.section_combo.currentText(), "Work")

    # -- toggling ----------------------------------------------------------
    def test_toggling_one_row_flips_it(self) -> None:
        self.select_rows(1)

        self.app.toggle_enabled()

        self.assertEqual(
            [expansion.enabled for expansion in self.app.store.expansions],
            [True, False, True],
        )

    def test_toggling_a_selection_turns_them_all_off(self) -> None:
        self.select_rows(0, 2)

        self.app.toggle_enabled()

        self.assertEqual(
            [expansion.enabled for expansion in self.app.store.expansions],
            [False, True, False],
        )

    def test_a_mixed_selection_ends_up_all_on(self) -> None:
        # Flipping each row in turn would leave the selection mixed the other
        # way round, which is no use for making a batch consistent.
        self.app.store.expansions[0].enabled = False
        self.app.refresh_expansions()
        self.select_rows(0, 1)

        self.app.toggle_enabled()

        self.assertEqual(
            [expansion.enabled for expansion in self.app.store.expansions],
            [True, True, True],
        )

    def test_the_selection_survives_a_toggle(self) -> None:
        # The refresh rebuilds every row, so a second press needs the selection
        # put back.
        self.select_rows(0, 2)

        self.app.toggle_enabled()

        self.assertEqual(self.app.selected_expansion_indexes(), [0, 2])

    def test_the_focused_row_survives_a_toggle(self) -> None:
        # A rebuilt table has no current row, which would silently move what
        # Edit opens from the row being pointed at to the topmost selected.
        self.select_rows(0, 2, focus=2)

        self.app.toggle_enabled()

        self.assertEqual(self.app.selected_expansion_index(), 2)

    def test_a_toggle_leaves_a_focus_behind_when_there_was_none(self) -> None:
        self.select_rows(1, 2)

        self.app.toggle_enabled()

        self.assertEqual(self.app.tree.currentRow(), 1)

    def test_toggling_nothing_says_so(self) -> None:
        self.app.tree.clearSelection()

        with mock.patch.object(app_module, "show_info") as reported:
            self.app.toggle_enabled()

        self.assertTrue(reported.called)
        self.assertTrue(all(e.enabled for e in self.app.store.expansions))

    # -- deleting ----------------------------------------------------------
    def test_deleting_a_selection_removes_exactly_those_rows(self) -> None:
        # Deleting from the front would shift the later indexes out from under
        # the loop and take the wrong expansion with them.
        self.select_rows(0, 2)

        with mock.patch.object(app_module, "confirm", return_value=True):
            self.app.delete_expansion()

        self.assertEqual(self.triggers(), [";two"])

    def test_a_refused_delete_keeps_everything(self) -> None:
        self.select_rows(0, 1)

        with mock.patch.object(app_module, "confirm", return_value=False):
            self.app.delete_expansion()

        self.assertEqual(self.triggers(), [";one", ";two", ";three"])

    def test_deleting_another_row_leaves_the_open_edits_alone(self) -> None:
        # Now that selecting a row does not load it, the row being deleted and
        # the expansion in the editor are two different things. Clearing the
        # form either way threw away edits nobody asked to discard.
        self.select_rows(0)
        self.app.load_selected_expansion()
        self.app.replacement_text.setPlainText("half typed")
        self.select_rows(2)

        with mock.patch.object(app_module, "confirm", return_value=True):
            self.app.delete_expansion()

        self.assertEqual(self.triggers(), [";one", ";two"])
        self.assertEqual(self.app.trigger_edit.text(), ";one")
        self.assertEqual(self.app.replacement_text.toPlainText(), "half typed")
        self.assertIs(self.app.current_expansion, self.app.store.expansions[0])

    def test_deleting_the_open_row_blanks_the_editor(self) -> None:
        self.select_rows(0)
        self.app.load_selected_expansion()

        with mock.patch.object(app_module, "confirm", return_value=True):
            self.app.delete_expansion()

        self.assertEqual(self.app.trigger_edit.text(), "")
        self.assertIsNone(self.app.current_expansion)

    def test_an_equal_looking_row_is_not_mistaken_for_the_open_one(self) -> None:
        # Duplicate triggers are allowed, so two records can compare equal
        # while being different rows. Only the one actually open should count.
        twin = Expansion("Work", ";one", "first")
        self.app.store.expansions.append(twin)
        self.app.refresh_expansions()
        self.select_rows(0)
        self.app.load_selected_expansion()
        self.select_rows(3)  # the twin

        with mock.patch.object(app_module, "confirm", return_value=True):
            self.app.delete_expansion()

        self.assertEqual(self.app.trigger_edit.text(), ";one")
        self.assertIs(self.app.current_expansion, self.app.store.expansions[0])

    def test_deleting_a_section_forgets_only_its_own_expansions(self) -> None:
        # The store drops the section's expansions, so an editor left pointing
        # at one would apply edits to a record no longer in the library.
        self.app.store.add_section("Spare")
        self.app.store.expansions.append(Expansion("Spare", ";spare", "spare text"))
        self.app.refresh_sections()
        self.select_rows(0)
        self.app.load_selected_expansion()
        self.app.replacement_text.setPlainText("half typed")
        self.app.selected_section = "Spare"

        with mock.patch.object(app_module, "confirm", return_value=True):
            self.app.delete_section()

        self.assertEqual(self.triggers(), [";one", ";two", ";three"])
        self.assertEqual(self.app.replacement_text.toPlainText(), "half typed")
        self.assertIs(self.app.current_expansion, self.app.store.expansions[0])

    def test_deleting_the_open_expansions_section_blanks_the_editor(self) -> None:
        self.app.store.add_section("Spare")
        self.app.refresh_sections()
        self.select_rows(0)
        self.app.load_selected_expansion()
        self.app.selected_section = "Work"

        with mock.patch.object(app_module, "confirm", return_value=True):
            self.app.delete_section()

        self.assertEqual(self.triggers(), [])
        self.assertIsNone(self.app.current_expansion)
        self.assertEqual(self.app.trigger_edit.text(), "")

    def test_deleting_nothing_says_so(self) -> None:
        self.app.tree.clearSelection()

        with mock.patch.object(app_module, "show_info") as reported:
            with mock.patch.object(app_module, "confirm") as asked:
                self.app.delete_expansion()

        self.assertTrue(reported.called)
        self.assertFalse(asked.called, "it asked before checking the selection")
        self.assertEqual(len(self.app.store.expansions), 3)


if __name__ == "__main__":
    unittest.main()
