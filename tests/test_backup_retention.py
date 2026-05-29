import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ahk_manager import Expansion, ExpansionStore, generate_ahk


class BackupRetentionTests(unittest.TestCase):
    def test_only_five_most_recent_generated_backups_are_kept(self) -> None:
        store = ExpansionStore(
            sections=["Common"],
            expansions=[Expansion("Common", "brb", "Be right back")],
        )

        with TemporaryDirectory() as temp_dir:
            ahk_path = Path(temp_dir) / "text_expansions.ahk"
            ahk_path.write_text("initial\n", encoding="utf-8")

            unrelated_files = [
                Path(temp_dir) / "text_expansions.old.bak.ahk",
                Path(temp_dir) / "other.20260529_120000.bak.ahk",
                Path(temp_dir) / "text_expansions.20260529_120000.bak.txt",
            ]
            for file_path in unrelated_files:
                file_path.write_text("keep me\n", encoding="utf-8")

            for index in range(8):
                store.expansions[0].replacement = f"Replacement {index}"
                generate_ahk(store, ahk_path, backup=True)

            backup_re = re.compile(r"^text_expansions\.\d{8}_\d{6}(?:_\d+)?\.bak\.ahk$")
            backups = [
                path
                for path in Path(temp_dir).glob("text_expansions.*.bak.ahk")
                if backup_re.match(path.name)
            ]
            self.assertEqual(len(backups), 5)
            self.assertTrue(all(path.exists() for path in unrelated_files))

    def test_backup_names_are_unique_when_generated_quickly(self) -> None:
        store = ExpansionStore(
            sections=["Common"],
            expansions=[Expansion("Common", "brb", "Be right back")],
        )

        with TemporaryDirectory() as temp_dir:
            ahk_path = Path(temp_dir) / "text_expansions.ahk"
            ahk_path.write_text("initial\n", encoding="utf-8")

            first_backup = generate_ahk(store, ahk_path, backup=True)
            store.expansions[0].replacement = "Updated"
            second_backup = generate_ahk(store, ahk_path, backup=True)

            self.assertIsNotNone(first_backup)
            self.assertIsNotNone(second_backup)
            self.assertNotEqual(first_backup, second_backup)
            self.assertTrue(first_backup.exists())
            self.assertTrue(second_backup.exists())


if __name__ == "__main__":
    unittest.main()
