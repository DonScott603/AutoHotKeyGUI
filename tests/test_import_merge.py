import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ahk_manager import Expansion, ExpansionStore, import_ahk, merge_imported_store


class ImportMergeTests(unittest.TestCase):
    def test_imported_sections_and_expansions_are_merged(self) -> None:
        target = ExpansionStore(
            sections=["Email"],
            expansions=[Expansion("Email", "sig", "Existing signature")],
        )
        imported = ExpansionStore(
            sections=["Email", "Common"],
            expansions=[
                Expansion("Email", "brb", "Be right back."),
                Expansion("Common", "addr", "123 Main St."),
            ],
        )

        result = merge_imported_store(target, imported)

        self.assertEqual(result.added, 2)
        self.assertEqual(target.sections, ["Email", "Common"])
        self.assertEqual([item.trigger for item in target.expansions], ["sig", "brb", "addr"])

    def test_duplicate_trigger_can_be_skipped(self) -> None:
        target = ExpansionStore(
            sections=["Email"],
            expansions=[Expansion("Email", "sig", "Existing signature")],
        )
        imported = ExpansionStore(
            sections=["Email"],
            expansions=[Expansion("Email", "sig", "Imported signature")],
        )

        result = merge_imported_store(target, imported, conflict_action="skip")

        self.assertEqual(result.conflicts, 1)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(len(target.expansions), 1)
        self.assertEqual(target.expansions[0].replacement, "Existing signature")

    def test_duplicate_trigger_can_be_overwritten(self) -> None:
        target = ExpansionStore(
            sections=["Email"],
            expansions=[Expansion("Email", "sig", "Existing signature", notes="old")],
        )
        imported = ExpansionStore(
            sections=["Email"],
            expansions=[Expansion("Email", "sig", "Imported signature", enabled=False, notes="new")],
        )

        result = merge_imported_store(target, imported, conflict_action="overwrite")

        self.assertEqual(result.overwritten, 1)
        self.assertEqual(len(target.expansions), 1)
        self.assertEqual(target.expansions[0].replacement, "Imported signature")
        self.assertFalse(target.expansions[0].enabled)
        self.assertEqual(target.expansions[0].notes, "new")

    def test_duplicate_trigger_can_be_renamed(self) -> None:
        target = ExpansionStore(
            sections=["Email"],
            expansions=[
                Expansion("Email", "sig", "Existing signature"),
                Expansion("Email", "sig_imported", "Previous import"),
            ],
        )
        imported = ExpansionStore(
            sections=["Email"],
            expansions=[Expansion("Email", "sig", "Imported signature")],
        )

        result = merge_imported_store(target, imported, conflict_action="rename")

        self.assertEqual(result.renamed, 1)
        self.assertEqual(target.expansions[-1].trigger, "sig_imported2")
        self.assertEqual(target.expansions[-1].replacement, "Imported signature")

    def test_import_ahk_then_merge_preserves_existing_data(self) -> None:
        target = ExpansionStore(
            sections=["Email"],
            expansions=[Expansion("Email", "sig", "Existing signature")],
        )

        with TemporaryDirectory() as temp_dir:
            ahk_path = Path(temp_dir) / "import.ahk"
            ahk_path.write_text("; === Email ===\n::brb::Be right back.\n", encoding="utf-8")
            imported = import_ahk(ahk_path)

        merge_imported_store(target, imported)

        self.assertEqual(len(target.expansions), 2)
        self.assertEqual(target.expansions[0].trigger, "sig")
        self.assertEqual(target.expansions[1].trigger, "brb")


if __name__ == "__main__":
    unittest.main()
