"""Key combinations an expansion presses -- {AHK_KEY:^+n} for Ctrl+Shift+N.

AHK_KEY began as Tab and Enter only. It now takes the notation a key
combination trigger uses, so one shortcut can press another, type some text
and press Enter. What it accepts stays a closed set: the key is sent as
"{name}", and a name AutoHotkey does not know would be typed as literal text.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

# Run Qt without a real display so the GUI tests work headlessly (e.g. in CI).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QKeyCombination, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication

import app as app_module
from ahk_manager import (
    Expansion,
    ExpansionStore,
    TemplatePlaceholder,
    collect_placeholder_summary,
    generate_ahk,
    import_ahk,
    key_press_display,
    normalize_key_press,
    parse_replacement_template,
    render_ahk,
)
from app import KeyPressDialog
from qt_cleanup import destroy_all_windows

# A single QApplication must exist for the lifetime of the process.
_qt_app = QApplication.instance() or QApplication([])

# The example the feature was asked for: Ctrl+Shift+L presses Ctrl+Shift+N,
# types a name and presses Enter.
_REQUESTED = "{AHK_KEY:^+n}Sample Name{AHK_KEY:Enter}"


def _store(*expansions: Expansion) -> ExpansionStore:
    return ExpansionStore(sections=["Common"], expansions=list(expansions))


class NotationTests(unittest.TestCase):
    def test_modifier_order_and_case_are_normalised(self) -> None:
        self.assertEqual(normalize_key_press("+^N"), "^+n")

    def test_the_original_keys_keep_their_spelling(self) -> None:
        # Libraries written before combinations existed hold exactly these.
        self.assertEqual(normalize_key_press("Tab"), "Tab")
        self.assertEqual(normalize_key_press("Enter"), "Enter")

    def test_keys_a_trigger_refuses_can_still_be_pressed(self) -> None:
        # A trigger needs Ctrl, Alt or Win so it cannot swallow typing. A
        # pressed key has no such worry.
        for value, expected in (("+tab", "+Tab"), ("f5", "F5"), ("escape", "Escape"), ("+a", "+a")):
            with self.subTest(value=value):
                self.assertEqual(normalize_key_press(value), expected)

    def test_a_bare_character_is_refused(self) -> None:
        for value in ("n", "7"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "ordinary typing"):
                normalize_key_press(value)

    def test_unknown_keys_are_refused(self) -> None:
        for value in ("Bogus", "^", "^;", "^jk"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "not a key"):
                normalize_key_press(value)

    def test_a_repeated_modifier_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "repeats a modifier"):
            normalize_key_press("^^n")

    def test_display(self) -> None:
        self.assertEqual(key_press_display("^+n"), "Ctrl+Shift+N")
        self.assertEqual(key_press_display("+Tab"), "Shift+Tab")
        self.assertEqual(key_press_display("Enter"), "Enter")

    def test_the_placeholder_holds_the_canonical_spelling(self) -> None:
        placeholder = parse_replacement_template("{AHK_KEY: +^N }")[0]
        assert isinstance(placeholder, TemplatePlaceholder)
        self.assertEqual(placeholder.value, "^+n")


class GenerationTests(unittest.TestCase):
    def test_a_hotkey_presses_a_combination_types_and_presses_enter(self) -> None:
        output = render_ahk(_store(Expansion("Common", "^+l", _REQUESTED, hotkey=True)))

        block = output.split("^+l::\n", 1)[1].split("\n}\n", 1)[0]
        press = block.index('SendEvent("^+{n}")')
        text = block.index('__tem_result .= "Sample Name"')
        enter = block.index('SendEvent("{Enter}")')
        self.assertLess(press, text)
        self.assertLess(text, enter)

    def test_a_named_key_with_a_modifier_keeps_its_braces(self) -> None:
        # "+Tab" unbraced would be Shift+T followed by the letters "ab".
        output = render_ahk(_store(Expansion("Common", ";back", "x{AHK_KEY:+Tab}")))

        self.assertIn('SendEvent("+{Tab}")', output)

    def test_an_unknown_key_stops_generation(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a key"):
            render_ahk(_store(Expansion("Common", ";bad", "{AHK_KEY:Bogus}")))

    def test_the_script_uses_the_keyboard_hook(self) -> None:
        # Without it a sent combination fires any expansion triggered by that
        # same combination -- itself included, without end.
        output = render_ahk(_store(Expansion("Common", "^+l", _REQUESTED, hotkey=True)))

        self.assertIn("\n#UseHook\n", output)

    def test_the_summary_names_the_combination(self) -> None:
        summary = collect_placeholder_summary(parse_replacement_template(_REQUESTED))

        self.assertIn("Keystrokes: Ctrl+Shift+N, Enter", summary)


class RoundTripTests(unittest.TestCase):
    def _round_trip(self, store: ExpansionStore) -> ExpansionStore:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "generated.ahk"
            generate_ahk(store, path, backup=False)
            return import_ahk(path)

    def test_a_hotkey_with_combinations_round_trips(self) -> None:
        imported = self._round_trip(_store(Expansion("Common", "^+l", _REQUESTED, hotkey=True)))

        self.assertEqual(len(imported.expansions), 1)
        self.assertTrue(imported.expansions[0].hotkey)
        self.assertEqual(imported.expansions[0].trigger, "^+l")
        self.assertEqual(imported.expansions[0].replacement, _REQUESTED)

    def test_a_hotstring_with_combinations_round_trips(self) -> None:
        replacement = "a{AHK_KEY:+Tab}b{AHK_KEY:F5}c{AHK_KEY:^!#Delete}"
        imported = self._round_trip(_store(Expansion("Common", ";keys", replacement)))

        self.assertEqual(imported.expansions[0].replacement, replacement)


class DialogTests(unittest.TestCase):
    def setUp(self) -> None:
        destroy_all_windows()

    def _dialog(self, combination: QKeyCombination | None) -> KeyPressDialog:
        dialog = KeyPressDialog(None)
        if combination is not None:
            dialog.key_edit.setKeySequence(QKeySequence(combination))
        return dialog

    def test_a_captured_combination_becomes_a_placeholder(self) -> None:
        dialog = self._dialog(
            QKeyCombination(
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
                Qt.Key.Key_N,
            )
        )

        dialog.accept()

        self.assertEqual(dialog.choice, "{AHK_KEY:^+n}")

    def test_a_bare_named_key_is_accepted(self) -> None:
        dialog = self._dialog(QKeyCombination(Qt.KeyboardModifier.NoModifier, Qt.Key.Key_Escape))

        dialog.accept()

        self.assertEqual(dialog.choice, "{AHK_KEY:Escape}")

    def test_nothing_usable_is_reported_and_inserts_nothing(self) -> None:
        cases = (
            None,
            QKeyCombination(Qt.KeyboardModifier.NoModifier, Qt.Key.Key_N),
            QKeyCombination(Qt.KeyboardModifier.ControlModifier, Qt.Key.Key_Semicolon),
        )
        for combination in cases:
            with self.subTest(combination=combination):
                dialog = self._dialog(combination)
                with mock.patch.object(app_module, "show_error") as show_error:
                    dialog.accept()

                show_error.assert_called_once()
                self.assertIsNone(dialog.choice)


if __name__ == "__main__":
    unittest.main()
