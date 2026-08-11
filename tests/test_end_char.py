"""The per-expansion setting that swallows the character which triggered it.

Typing ";ty " normally leaves "Thank you! " -- the space that fired the
hotstring is reproduced after the replacement. With omit_end_char set the
expansion ends exactly where its replacement does.

The two generated forms suppress it differently, so both are covered here: a
static auto-replace hotstring takes AutoHotkey's "O" option, while a block
hotstring simply stops re-sending A_EndChar.
"""

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

# Run Qt without a real display so the GUI tests work headlessly (e.g. in CI).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import app as app_module
from ahk_manager import (
    Expansion,
    ExpansionStore,
    generate_ahk,
    import_ahk,
    render_ahk,
    resolve_expansion_preview,
)
from app import ExpansionApp
from qt_cleanup import destroy_all_windows

# A single QApplication must exist for the lifetime of the process.
_qt_app = QApplication.instance() or QApplication([])


def _store(*expansions: Expansion) -> ExpansionStore:
    return ExpansionStore(sections=["Common"], expansions=list(expansions))


class StaticHotstringTests(unittest.TestCase):
    def test_the_option_is_added_when_the_ending_character_is_dropped(self) -> None:
        output = render_ahk(_store(Expansion("Common", ";ty", "Thank you!", omit_end_char=True)))

        self.assertIn(":CTO:;ty::Thank you!", output)

    def test_the_option_is_absent_by_default(self) -> None:
        output = render_ahk(_store(Expansion("Common", ";ty", "Thank you!")))

        self.assertIn(":CT:;ty::Thank you!", output)
        self.assertNotIn(":CTO:", output)


class BlockHotstringTests(unittest.TestCase):
    """The forms that run code: multiline, paste-delivered and dynamic.

    None of these auto-replace, so AutoHotkey never reproduces the ending
    character itself -- the generated block re-sends A_EndChar to match the
    static form. Dropping the character means dropping those lines, and "O"
    would have nothing to do here.
    """

    def test_a_multiline_expansion_drops_the_ending_character(self) -> None:
        output = render_ahk(
            _store(Expansion("Common", ";sig", "first\nsecond", omit_end_char=True))
        )

        self.assertIn('SendText("first`nsecond")', output)
        self.assertNotIn("A_EndChar", output)

    def test_a_multiline_expansion_keeps_it_by_default(self) -> None:
        output = render_ahk(_store(Expansion("Common", ";sig", "first\nsecond")))

        self.assertIn("SendText(A_EndChar)", output)

    def test_a_dynamic_expansion_drops_the_ending_character(self) -> None:
        output = render_ahk(
            _store(
                Expansion(
                    "Common",
                    ";ld",
                    '{AHK_EXPR:FormatTime(A_Now, "yyyy-MM-dd")}',
                    omit_end_char=True,
                )
            )
        )

        self.assertIn("FormatTime", output)
        self.assertNotIn("A_EndChar", output)

    def test_a_dynamic_expansion_keeps_it_by_default(self) -> None:
        output = render_ahk(
            _store(Expansion("Common", ";ld", '{AHK_EXPR:FormatTime(A_Now, "yyyy-MM-dd")}'))
        )

        self.assertIn("SendText(A_EndChar)", output)


class RoundTripTests(unittest.TestCase):
    def _round_trip(self, expansion: Expansion) -> Expansion:
        with TemporaryDirectory() as temp_dir:
            ahk_path = Path(temp_dir) / "gen.ahk"
            generate_ahk(_store(expansion), ahk_path, backup=False)
            return import_ahk(ahk_path).expansions[0]

    def test_a_static_expansion_round_trips(self) -> None:
        imported = self._round_trip(
            Expansion("Common", ";ty", "Thank you!", omit_end_char=True)
        )

        self.assertEqual(imported.replacement, "Thank you!")
        self.assertTrue(imported.omit_end_char)

    def test_a_dynamic_expansion_round_trips(self) -> None:
        # Nothing on the generated hotstring line records the setting here --
        # it shows up only as the absence of the A_EndChar lines -- so this
        # relies on the source marker carrying it.
        imported = self._round_trip(
            Expansion(
                "Common",
                ";ld",
                '{AHK_EXPR:FormatTime(A_Now, "yyyy-MM-dd")}',
                omit_end_char=True,
            )
        )

        self.assertEqual(imported.replacement, '{AHK_EXPR:FormatTime(A_Now, "yyyy-MM-dd")}')
        self.assertTrue(imported.omit_end_char)

    def test_an_unset_expansion_round_trips_as_unset(self) -> None:
        self.assertFalse(self._round_trip(Expansion("Common", ";ty", "Thank you!")).omit_end_char)

    def test_the_library_file_round_trips(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "expansions.json"
            _store(
                Expansion("Common", ";ty", "Thank you!", omit_end_char=True),
                Expansion("Common", ";brb", "Be right back"),
            ).save(path)
            loaded = ExpansionStore.load(path)

        self.assertTrue(loaded.expansions[0].omit_end_char)
        self.assertFalse(loaded.expansions[1].omit_end_char)

    def test_a_non_boolean_setting_is_refused(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "expansions.json"
            path.write_text(
                json.dumps(
                    {
                        "sections": ["Common"],
                        "expansions": [
                            {
                                "section": "Common",
                                "trigger": ";ty",
                                "replacement": "Thank you!",
                                "omit_end_char": "yes",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as caught:
                ExpansionStore.load(path)

        self.assertIn("omit_end_char", str(caught.exception))


class HandWrittenScriptTests(unittest.TestCase):
    """"O" is AutoHotkey's own option, so a hand-written script already says it."""

    def _imported(self, line: str) -> Expansion:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hand.ahk"
            path.write_text(f"; === Common ===\n{line}\n", encoding="utf-8")
            return import_ahk(path).expansions[0]

    def test_the_option_is_read_from_a_hand_written_hotstring(self) -> None:
        self.assertTrue(self._imported(":O:;ty::Thank you!").omit_end_char)

    def test_the_option_is_matched_whatever_its_case_or_position(self) -> None:
        self.assertTrue(self._imported(":co:;ty::Thank you!").omit_end_char)

    def test_a_hotstring_without_it_imports_unset(self) -> None:
        self.assertFalse(self._imported(":C:;ty::Thank you!").omit_end_char)


class PreviewTests(unittest.TestCase):
    def test_the_preview_reports_the_setting(self) -> None:
        store = _store(Expansion("Common", ";ty", "Thank you!", omit_end_char=True))

        self.assertIn(
            "Keeps ending character: No", resolve_expansion_preview(store.expansions[0], store).content
        )

    def test_the_preview_reports_the_default(self) -> None:
        store = _store(Expansion("Common", ";ty", "Thank you!"))

        self.assertIn(
            "Keeps ending character: Yes",
            resolve_expansion_preview(store.expansions[0], store).content,
        )


class EditorTests(unittest.TestCase):
    """The checkbox that carries the setting, from the list into the form and back."""

    def setUp(self) -> None:
        destroy_all_windows()
        self._temp = TemporaryDirectory()
        root = Path(self._temp.name)
        ExpansionStore(
            sections=["Work"],
            expansions=[
                Expansion("Work", ";ty", "Thank you!", omit_end_char=True),
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

    def _by_trigger(self, trigger: str) -> Expansion:
        return next(item for item in self.app.store.expansions if item.trigger == trigger)

    def test_opening_an_expansion_shows_its_setting(self) -> None:
        self._open(0)

        self.assertTrue(self.app.omit_end_char_check.isChecked())

    def test_opening_another_clears_it_again(self) -> None:
        # The box is not reset between rows on its own, so an expansion that
        # drops its ending character would otherwise leave the next one looking
        # as though it did too.
        self._open(0)

        self._open(1)

        self.assertFalse(self.app.omit_end_char_check.isChecked())

    def test_ticking_it_reaches_the_stored_expansion(self) -> None:
        self._open(1)

        self.app.omit_end_char_check.setChecked(True)
        self.app.apply_form()

        self.assertTrue(self._by_trigger(";brb").omit_end_char)

    def test_clearing_it_reaches_the_stored_expansion(self) -> None:
        self._open(0)

        self.app.omit_end_char_check.setChecked(False)
        self.app.apply_form()

        self.assertFalse(self._by_trigger(";ty").omit_end_char)

    def test_a_new_expansion_starts_unset(self) -> None:
        self._open(0)

        self.app.new_expansion()

        self.assertFalse(self.app.omit_end_char_check.isChecked())


if __name__ == "__main__":
    unittest.main()
