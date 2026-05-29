import unittest
from pathlib import Path

from app import command_line_references_script, extract_ahk_script_paths, normalized_path_for_compare


class ReloadMatchingTests(unittest.TestCase):
    def test_command_line_matches_exact_configured_script_path(self) -> None:
        target = Path(r"C:\Users\donal\Projects\AutoHotKeyGUI\text_expansions.ahk")
        command_line = (
            r'"C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe" '
            r'"C:\Users\donal\Projects\AutoHotKeyGUI\text_expansions.ahk"'
        )

        self.assertTrue(command_line_references_script(command_line, target))

    def test_command_line_does_not_match_unrelated_script(self) -> None:
        target = Path(r"C:\Users\donal\Projects\AutoHotKeyGUI\text_expansions.ahk")
        command_line = (
            r'"C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe" '
            r'"C:\Users\donal\Projects\Other\text_expansions.ahk"'
        )

        self.assertFalse(command_line_references_script(command_line, target))

    def test_path_normalization_handles_relative_and_absolute_paths(self) -> None:
        self.assertEqual(
            normalized_path_for_compare(Path("text_expansions.ahk")),
            normalized_path_for_compare(Path.cwd() / "text_expansions.ahk"),
        )

    def test_extract_ahk_script_paths_handles_spaces(self) -> None:
        command_line = (
            r'"C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe" '
            r'"C:\Users\donal\My Scripts\text expansions.ahk"'
        )

        self.assertEqual(
            extract_ahk_script_paths(command_line),
            [r"C:\Users\donal\My Scripts\text expansions.ahk"],
        )


if __name__ == "__main__":
    unittest.main()
