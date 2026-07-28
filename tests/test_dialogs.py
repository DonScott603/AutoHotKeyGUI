import os
import unittest

# Run Qt without a real display so the GUI tests work headlessly (e.g. in CI).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget

import app as app_module
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


class TitleBarThemeFilterTests(unittest.TestCase):
    """Windows colours a title bar per window, from an attribute Qt does not
    manage, so theming the main window leaves every dialog's bar untouched. The
    filter is installed on the application because the QMessageBox convenience
    statics build and run their dialog internally and never return it.
    """

    def setUp(self) -> None:
        self.themed: list[str] = []
        self._real = app_module.apply_titlebar_theme
        app_module.apply_titlebar_theme = (  # type: ignore[assignment]
            lambda widget, repaint=False: self.themed.append(
                widget.windowTitle() or widget.__class__.__name__
            )
        )
        self.addCleanup(
            lambda: setattr(app_module, "apply_titlebar_theme", self._real)
        )

    def test_a_message_box_is_themed_when_shown(self) -> None:
        box = QMessageBox()
        box.setWindowTitle("Generate & Run AHK")
        filt = app_module.TitleBarThemeFilter()
        try:
            filt.eventFilter(box, QEvent(QEvent.Type.Show))

            self.assertEqual(self.themed, ["Generate & Run AHK"])
        finally:
            box.deleteLater()

    def test_a_child_widget_is_left_alone(self) -> None:
        # Only top-level windows have a title bar; theming every widget shown
        # would mean a DWM call per label.
        parent = QWidget()
        child = QWidget(parent)
        filt = app_module.TitleBarThemeFilter()
        try:
            filt.eventFilter(child, QEvent(QEvent.Type.Show))

            self.assertEqual(self.themed, [])
        finally:
            parent.deleteLater()

    def test_other_events_are_ignored(self) -> None:
        box = QMessageBox()
        filt = app_module.TitleBarThemeFilter()
        try:
            filt.eventFilter(box, QEvent(QEvent.Type.Hide))

            self.assertEqual(self.themed, [])
        finally:
            box.deleteLater()

    def test_the_filter_never_swallows_the_event(self) -> None:
        box = QMessageBox()
        filt = app_module.TitleBarThemeFilter()
        try:
            self.assertFalse(filt.eventFilter(box, QEvent(QEvent.Type.Show)))
        finally:
            box.deleteLater()


if __name__ == "__main__":
    unittest.main()
