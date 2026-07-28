import os
import unittest

# Run Qt without a real display so the GUI tests work headlessly (e.g. in CI).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from app import DateTimeDialog, ImportConflictDialog

# A single QApplication must exist for the lifetime of the process.
_qt_app = QApplication.instance() or QApplication([])


class DialogChoiceTests(unittest.TestCase):
    """The dialogs report their selection without shadowing Qt's own API.

    They used to store it as self.result, which replaced QDialog.result() on
    the instance -- so calling it, the obvious thing for anyone used to Qt,
    raised "'str' object is not callable" instead of returning the dialog code.
    """

    def test_import_conflict_dialog_reports_the_chosen_action(self) -> None:
        dialog = ImportConflictDialog(None, 3)
        try:
            dialog._rename.setChecked(True)
            dialog.accept()

            self.assertEqual(dialog.choice, "rename")
        finally:
            dialog.deleteLater()

    def test_import_conflict_dialog_defaults_to_skipping(self) -> None:
        dialog = ImportConflictDialog(None, 1)
        try:
            dialog.accept()

            self.assertEqual(dialog.choice, "skip")
        finally:
            dialog.deleteLater()

    def test_choice_is_unset_until_the_dialog_is_accepted(self) -> None:
        dialog = ImportConflictDialog(None, 1)
        try:
            self.assertIsNone(dialog.choice)
        finally:
            dialog.deleteLater()

    def test_qdialog_result_is_still_callable(self) -> None:
        # The reason for the rename: this used to raise TypeError.
        for dialog in (ImportConflictDialog(None, 1), DateTimeDialog(None)):
            try:
                dialog.accept()

                self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
            finally:
                dialog.deleteLater()

    def test_date_time_dialog_builds_an_expression_placeholder(self) -> None:
        dialog = DateTimeDialog(None)
        try:
            dialog.accept()

            self.assertIsNotNone(dialog.choice)
            assert dialog.choice is not None
            self.assertTrue(dialog.choice.startswith("{AHK_EXPR:FormatTime("))
        finally:
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
