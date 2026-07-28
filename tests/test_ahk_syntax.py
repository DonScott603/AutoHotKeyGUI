"""Parse the generated script with AutoHotkey itself.

The rest of the suite asserts on generated text, which cannot tell whether
AutoHotkey will actually accept it -- a semicolon escaping bug shipped a file
that every Python test passed and AutoHotkey refused to load. These tests hand
the output to the real interpreter in /validate mode, which parses without
running. They skip where AutoHotkey is not installed, so a checkout without it
still runs green.
"""

import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ahk_manager import Expansion, ExpansionStore, VariableDef, generate_ahk

_CANDIDATES = (
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "AutoHotkey/v2/AutoHotkey.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "AutoHotkey/v2/AutoHotkey.exe",
)


def _find_autohotkey() -> Path | None:
    for candidate in _CANDIDATES:
        if candidate.is_file():
            return candidate
    found = shutil.which("AutoHotkey.exe") or shutil.which("AutoHotkey")
    return Path(found) if found else None


AHK = _find_autohotkey()


@unittest.skipUnless(AHK, "AutoHotkey v2 is not installed")
class AhkSyntaxTests(unittest.TestCase):
    def _validate(self, script: Path) -> subprocess.CompletedProcess[str]:
        assert AHK is not None
        # /ErrorStdOut is not optional here: without it a script that fails to
        # parse raises a modal error dialog and the run blocks until someone
        # dismisses it, which hangs an unattended suite rather than failing it.
        return subprocess.run(
            [str(AHK), "/ErrorStdOut", "/validate", str(script)],
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_validator_rejects_a_broken_script(self) -> None:
        # Guards the tests below: a validator that passed everything would make
        # them meaningless.
        with TemporaryDirectory() as temp_dir:
            broken = Path(temp_dir) / "broken.ahk"
            broken.write_text("#Requires AutoHotkey v2.0\nMsgBox(\n", encoding="utf-8")

            self.assertNotEqual(self._validate(broken).returncode, 0)

    def _awkward_store(self) -> ExpansionStore:
        return ExpansionStore(
            sections=["Work", "Status"],
            expansions=[
                # Triggers are conventionally semicolon-prefixed, and a
                # semicolon after whitespace opens a comment.
                Expansion(
                    "Work",
                    ";achs",
                    "Client Requested {AHK_INPUT:when|Settlement Date|Date|}, Please Process",
                ),
                Expansion("Work", ";note", "See ; footnote {AHK_INPUT:n|Num ; here|Num|}"),
                Expansion(
                    "Status",
                    ";st",
                    "{AHK_SELECT:state|Pick ; one|State|Open ; now||Closed}",
                ),
                # Quotes, backticks and a newline all need escaping too.
                Expansion("Work", ";q", 'He said "hi" and `tick\nnext line'),
                Expansion("Work", ";ld", '{AHK_EXPR:FormatTime(A_Now, "yyyy-MM-dd")}'),
            ],
            variables=[
                VariableDef("state", "list_selection", "Pick ; one", "", ["A ; b", "C"], ""),
            ],
        )

    def test_generated_script_parses_in_both_themes(self) -> None:
        store = self._awkward_store()
        with TemporaryDirectory() as temp_dir:
            for theme in ("light", "dark"):
                script = Path(temp_dir) / f"{theme}.ahk"
                generate_ahk(store, script, backup=False, theme=theme)

                result = self._validate(script)
                self.assertEqual(
                    result.returncode, 0, f"{theme} script failed to parse:\n{result.stderr}"
                )

    def test_static_only_script_parses(self) -> None:
        store = ExpansionStore(
            sections=["General"],
            expansions=[Expansion("General", ";brb", "Be right back ; shortly")],
        )
        with TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "static.ahk"
            generate_ahk(store, script, backup=False)

            result = self._validate(script)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
