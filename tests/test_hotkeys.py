"""Expansions fired by a key combination -- Ctrl+J -- rather than by typed text.

A key combination becomes an AutoHotkey hotkey ("^j::") instead of a hotstring.
The two differ in more than the header line: a hotkey has no auto-replace form,
no ending character to replay, and AutoHotkey refuses a whole script that
defines one twice, where a repeated hotstring loads and the last one wins.
"""

import json
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
    generate_ahk,
    hotkey_display,
    import_ahk,
    merge_imported_store,
    normalize_hotkey,
    placeholder_problems,
    render_ahk,
    resolve_expansion_preview,
)
from app import ExpansionApp, hotkey_from_sequence, sequence_from_hotkey
from qt_cleanup import destroy_all_windows

# A single QApplication must exist for the lifetime of the process.
_qt_app = QApplication.instance() or QApplication([])


def _store(*expansions: Expansion) -> ExpansionStore:
    return ExpansionStore(sections=["Common"], expansions=list(expansions))


class NotationTests(unittest.TestCase):
    def test_modifier_order_and_case_are_normalised(self) -> None:
        self.assertEqual(normalize_hotkey("!^J"), "^!j")

    def test_named_keys_take_one_spelling(self) -> None:
        self.assertEqual(normalize_hotkey("^pgdn"), "^PgDn")
        self.assertEqual(normalize_hotkey("^esc"), "^Escape")
        self.assertEqual(normalize_hotkey("^numpad3"), "^Numpad3")

    def test_shift_with_a_function_key_is_allowed(self) -> None:
        self.assertEqual(normalize_hotkey("+f5"), "+F5")

    def test_ordinary_typing_is_refused(self) -> None:
        # Each would stop its character reaching any application.
        for trigger in ("j", "+j", "F5", "+1"):
            with self.subTest(trigger=trigger), self.assertRaises(ValueError):
                normalize_hotkey(trigger)

    def test_keys_without_a_name_here_are_refused(self) -> None:
        for trigger in ("^", "^;", "^jk", "^^j"):
            with self.subTest(trigger=trigger), self.assertRaises(ValueError):
                normalize_hotkey(trigger)

    def test_display(self) -> None:
        self.assertEqual(hotkey_display("^!j"), "Ctrl+Alt+J")
        self.assertEqual(hotkey_display("#+F5"), "Shift+Win+F5")
        self.assertEqual(hotkey_display("!PgUp"), "Alt+PgUp")

    def test_display_falls_back_to_the_stored_text(self) -> None:
        self.assertEqual(hotkey_display("^;"), "^;")


class GenerationTests(unittest.TestCase):
    def test_static_text_takes_the_block_form(self) -> None:
        output = render_ahk(_store(Expansion("Common", "^j", "hello", hotkey=True)))

        self.assertIn('^j::\n{\n    SendText("hello")\n}', output)
        self.assertNotIn(":C", output.split("; === Common ===")[1])

    def test_no_ending_character_is_replayed(self) -> None:
        output = render_ahk(
            _store(
                Expansion("Common", "^j", "hello", hotkey=True),
                Expansion("Common", "!d", '{AHK_EXPR:FormatTime(A_Now, "yyyy-MM-dd")}', hotkey=True),
            )
        )

        self.assertNotIn("A_EndChar", output)

    def test_a_prompt_window_is_titled_with_the_readable_combination(self) -> None:
        output = render_ahk(
            _store(Expansion("Common", "^!j", "Dear {AHK_INPUT:name|Name|Name|}", hotkey=True))
        )

        self.assertIn('TEM_Form("Text Expansion Manager - Ctrl+Alt+J"', output)

    def test_a_disabled_hotkey_is_commented_out(self) -> None:
        output = render_ahk(_store(Expansion("Common", "^j", "hello", enabled=False, hotkey=True)))

        self.assertIn("; ^j::", output)
        self.assertNotIn("\n^j::", output)

    def test_a_typed_trigger_spelled_like_a_hotkey_stays_a_hotstring(self) -> None:
        self.assertIn(":CT:^j::hello", render_ahk(_store(Expansion("Common", "^j", "hello"))))

    def test_an_invalid_combination_refuses_to_generate(self) -> None:
        store = _store(Expansion("Common", "+j", "hello", hotkey=True))

        self.assertTrue(placeholder_problems(store))
        with self.assertRaises(ValueError):
            render_ahk(store)


class DuplicateTests(unittest.TestCase):
    def test_the_last_enabled_duplicate_keeps_the_combination(self) -> None:
        output = render_ahk(
            ExpansionStore(
                sections=["One", "Two"],
                expansions=[
                    Expansion("Two", "^J", "second", hotkey=True),
                    Expansion("One", "^j", "first", hotkey=True),
                ],
            )
        )

        # Generated in section order, so "Two" is last despite coming first.
        self.assertIn('SendText("second")', output)
        self.assertNotIn('SendText("first")', output)
        self.assertIn('; Skipped "^j"', output)

    def test_a_disabled_duplicate_does_not_win(self) -> None:
        output = render_ahk(
            _store(
                Expansion("Common", "^j", "first", hotkey=True),
                Expansion("Common", "^j", "second", enabled=False, hotkey=True),
            )
        )

        self.assertIn('    SendText("first")', output)

    def test_an_empty_duplicate_does_not_win(self) -> None:
        # It renders as skipped anyway, so letting it win would leave the
        # combination with nothing at all.
        output = render_ahk(
            _store(
                Expansion("Common", "^j", "first", hotkey=True),
                Expansion("Common", "^j", "", hotkey=True),
            )
        )

        self.assertIn('SendText("first")', output)

    def test_a_superseded_duplicate_survives_a_round_trip(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "gen.ahk"
            generate_ahk(
                _store(
                    Expansion("Common", "^j", "first", hotkey=True),
                    Expansion("Common", "^j", "second", hotkey=True),
                ),
                path,
                backup=False,
            )
            imported = import_ahk(path).expansions

        self.assertEqual(
            sorted((item.replacement, item.hotkey) for item in imported),
            [("first", True), ("second", True)],
        )

    def test_duplicates_are_grouped_by_combination_not_by_spelling(self) -> None:
        store = _store(
            Expansion("Common", "!^j", "first", hotkey=True),
            Expansion("Common", "^!J", "second", hotkey=True),
            Expansion("Common", "^!j", "typed"),
        )

        groups = list(store.duplicate_triggers().values())

        self.assertEqual(len(groups), 1)
        self.assertEqual([item.replacement for item in groups[0]], ["first", "second"])


class RoundTripTests(unittest.TestCase):
    def _round_trip(self, *expansions: Expansion) -> list[Expansion]:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "gen.ahk"
            generate_ahk(_store(*expansions), path, backup=False)
            return import_ahk(path).expansions

    def test_static_dynamic_and_disabled_hotkeys_round_trip(self) -> None:
        originals = [
            Expansion("Common", "^j", "hello", notes="greeting", hotkey=True),
            Expansion("Common", "!l", "Dear {AHK_INPUT:name|Name|Name|}", hotkey=True),
            Expansion("Common", "^+F5", "off", enabled=False, hotkey=True),
        ]

        imported = self._round_trip(*originals)

        self.assertEqual([item.to_dict() for item in imported], [item.to_dict() for item in originals])

    def test_a_skipped_hotkey_round_trips(self) -> None:
        (imported,) = self._round_trip(Expansion("Common", "^j", "", hotkey=True))

        self.assertTrue(imported.hotkey)
        self.assertEqual(imported.trigger, "^j")

    def test_a_hand_written_hotkey_is_left_out(self) -> None:
        # Its block is arbitrary code, with no template to read back.
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hand.ahk"
            path.write_text(
                '; === Common ===\n^j::\n{\n    Run("notepad")\n}\n:C:;ty::Thank you!\n',
                encoding="utf-8",
            )
            imported = import_ahk(path).expansions

        self.assertEqual([item.trigger for item in imported], [";ty"])

    def test_a_library_without_the_field_loads_as_typed_triggers(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "expansions.json"
            path.write_text(
                json.dumps(
                    {
                        "sections": ["Common"],
                        "expansions": [
                            {"section": "Common", "trigger": ";ty", "replacement": "Thank you!"}
                        ],
                    }
                ),
                encoding="utf-8",
            )

            self.assertFalse(ExpansionStore.load(path).expansions[0].hotkey)

    def test_a_non_boolean_field_is_refused(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "expansions.json"
            path.write_text(
                json.dumps(
                    {
                        "sections": ["Common"],
                        "expansions": [
                            {"section": "Common", "trigger": "^j", "replacement": "x", "hotkey": "yes"}
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as caught:
                ExpansionStore.load(path)

        self.assertIn("hotkey", str(caught.exception))


class ImportMergeTests(unittest.TestCase):
    def test_a_typed_trigger_does_not_clash_with_the_same_spelled_hotkey(self) -> None:
        target = _store(Expansion("Common", "^j", "typed"))

        result = merge_imported_store(
            target, _store(Expansion("Common", "^j", "keys", hotkey=True)), "skip"
        )

        self.assertEqual(result.added, 1)

    def test_a_clashing_hotkey_comes_in_switched_off_when_renaming(self) -> None:
        # "^j_imported" is not a key, so there is nothing to rename it to.
        target = _store(Expansion("Common", "^j", "mine", hotkey=True))

        merge_imported_store(
            target, _store(Expansion("Common", "^J", "theirs", hotkey=True)), "rename"
        )

        imported = target.expansions[1]
        self.assertEqual((imported.trigger, imported.enabled), ("^J", False))


class PreviewTests(unittest.TestCase):
    def test_the_preview_names_the_combination(self) -> None:
        store = _store(Expansion("Common", "^j", "hello", hotkey=True))

        result = resolve_expansion_preview(store.expansions[0], store)

        self.assertIn("Trigger: Ctrl+J (key combination)", result.content)
        self.assertNotIn("Keeps ending character", result.content)
        self.assertEqual(result.title, "Expansion: Ctrl+J")


class KeySequenceTests(unittest.TestCase):
    def test_stored_combinations_round_trip_through_the_capture(self) -> None:
        for trigger in ("^j", "^!j", "+F5", "#Numpad3", "!PgDn", "^Enter", "^+1"):
            with self.subTest(trigger=trigger):
                self.assertEqual(hotkey_from_sequence(sequence_from_hotkey(trigger)), trigger)

    def test_a_key_with_no_name_here_is_not_captured(self) -> None:
        sequence = QKeySequence(
            QKeyCombination(Qt.KeyboardModifier.ControlModifier, Qt.Key.Key_Semicolon)
        )

        self.assertIsNone(hotkey_from_sequence(sequence))

    def test_an_empty_capture_is_nothing(self) -> None:
        self.assertIsNone(hotkey_from_sequence(QKeySequence()))


class EditorTests(unittest.TestCase):
    def setUp(self) -> None:
        destroy_all_windows()
        self._temp = TemporaryDirectory()
        root = Path(self._temp.name)
        ExpansionStore(
            sections=["Work"],
            expansions=[
                Expansion("Work", "^j", "hello", hotkey=True),
                Expansion("Work", ";brb", "Be right back"),
            ],
        ).save(root / "expansions.json")
        self._saved_paths = (
            app_module.JSON_PATH,
            app_module.SETTINGS_PATH,
            app_module.UI_PREFS_PATH,
            app_module.AHK_PATH,
            app_module.DEFAULT_BACKUP_DIR,
        )
        app_module.JSON_PATH = root / "expansions.json"
        app_module.SETTINGS_PATH = root / "settings.json"
        app_module.UI_PREFS_PATH = root / "ui_prefs.json"
        app_module.AHK_PATH = root / "text_expansions.ahk"
        app_module.DEFAULT_BACKUP_DIR = root / "backups"
        self.app = ExpansionApp()

    def tearDown(self) -> None:
        self.app.close()
        (
            app_module.JSON_PATH,
            app_module.SETTINGS_PATH,
            app_module.UI_PREFS_PATH,
            app_module.AHK_PATH,
            app_module.DEFAULT_BACKUP_DIR,
        ) = self._saved_paths
        self._temp.cleanup()

    def _open(self, row: int) -> None:
        self.app.tree.selectRow(row)
        self.app.load_selected_expansion()

    def _press(self, modifiers: Qt.KeyboardModifier, key: Qt.Key) -> None:
        self.app.hotkey_edit.setKeySequence(QKeySequence(QKeyCombination(modifiers, key)))

    def test_the_table_shows_the_readable_combination(self) -> None:
        item = self.app.tree.item(0, 1)
        assert item is not None
        self.assertEqual(item.text(), "Ctrl+J")

    def test_opening_a_hotkey_shows_the_capture(self) -> None:
        self._open(0)

        self.assertTrue(self.app.hotkey_trigger_radio.isChecked())
        self.assertEqual(hotkey_from_sequence(self.app.hotkey_edit.keySequence()), "^j")
        self.assertFalse(self.app.omit_end_char_check.isEnabled())

    def test_opening_a_typed_trigger_after_it_switches_back(self) -> None:
        self._open(0)

        self._open(1)

        self.assertTrue(self.app.typed_trigger_radio.isChecked())
        self.assertTrue(self.app.omit_end_char_check.isEnabled())

    def test_a_captured_combination_reaches_the_stored_expansion(self) -> None:
        self._open(1)

        self.app.hotkey_trigger_radio.setChecked(True)
        self._press(
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier, Qt.Key.Key_B
        )
        self.app.apply_form()

        stored = next(item for item in self.app.store.expansions if item.replacement == "Be right back")
        self.assertEqual((stored.trigger, stored.hotkey), ("^!b", True))

    def test_a_combination_that_would_stop_typing_is_refused(self) -> None:
        self.app.new_expansion()
        self.app.hotkey_trigger_radio.setChecked(True)
        self._press(Qt.KeyboardModifier.ShiftModifier, Qt.Key.Key_B)
        self.app.replacement_text.setPlainText("x")

        with self.assertRaises(ValueError):
            self.app.read_form()

    def test_an_empty_capture_is_refused(self) -> None:
        self.app.new_expansion()
        self.app.hotkey_trigger_radio.setChecked(True)
        self.app.replacement_text.setPlainText("x")

        with self.assertRaises(ValueError):
            self.app.read_form()

    def test_a_duplicate_combination_warns(self) -> None:
        self.app.new_expansion()
        self.app.hotkey_trigger_radio.setChecked(True)
        self._press(Qt.KeyboardModifier.ControlModifier, Qt.Key.Key_J)
        self.app.replacement_text.setPlainText("again")

        with mock.patch.object(app_module, "show_warning") as warned:
            self.app.apply_form()

        self.assertEqual(warned.call_args.args[1], "Duplicate key combination")

    def test_a_common_shortcut_warns(self) -> None:
        self.app.new_expansion()
        self.app.hotkey_trigger_radio.setChecked(True)
        self._press(Qt.KeyboardModifier.ControlModifier, Qt.Key.Key_C)
        self.app.replacement_text.setPlainText("x")

        with mock.patch.object(app_module, "show_warning") as warned:
            self.app.apply_form()

        self.assertEqual(warned.call_args.args[1], "Common shortcut")


if __name__ == "__main__":
    unittest.main()
