import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ahk_manager import (
    Expansion,
    ExpansionStore,
    generate_ahk,
    import_ahk,
    merge_imported_store,
)


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

    def test_case_variants_import_without_conflict(self) -> None:
        target = ExpansionStore(
            sections=["Common"],
            expansions=[Expansion("Common", "Hsa", "Has")],
        )
        imported = ExpansionStore(
            sections=["Common"],
            expansions=[Expansion("Common", "hsa", "has")],
        )

        result = merge_imported_store(target, imported)

        self.assertEqual(result.conflicts, 0)
        self.assertEqual(result.added, 1)
        self.assertEqual([item.trigger for item in target.expansions], ["Hsa", "hsa"])


    def test_generated_dynamic_expansion_round_trips_through_import(self) -> None:
        # A date/variable expansion generates as an empty-replacement hotstring
        # plus a code block. The embedded source marker must let it re-import
        # with its original template intact (generate -> import -> generate).
        store = ExpansionStore(
            sections=["Dates", "Work"],
            expansions=[
                Expansion("Dates", ";ld", '{AHK_EXPR:FormatTime(A_Now, "yyyy-MM-dd")}'),
                Expansion("Work", ";ask", "Hello {AHK_INPUT:name|Enter name|Name|}", notes="prompt"),
            ],
        )

        with TemporaryDirectory() as temp_dir:
            ahk_path = Path(temp_dir) / "gen.ahk"
            generate_ahk(store, ahk_path, backup=False)
            imported = import_ahk(ahk_path)

        by_trigger = {item.trigger: item for item in imported.expansions}
        self.assertEqual(by_trigger[";ld"].replacement, '{AHK_EXPR:FormatTime(A_Now, "yyyy-MM-dd")}')
        self.assertEqual(by_trigger[";ask"].replacement, "Hello {AHK_INPUT:name|Enter name|Name|}")
        self.assertEqual(by_trigger[";ask"].notes, "prompt")

    def test_static_expansion_round_trips_with_notes(self) -> None:
        store = ExpansionStore(
            sections=["General"],
            expansions=[Expansion("General", "brb", "Be right back!", notes="quick")],
        )

        with TemporaryDirectory() as temp_dir:
            ahk_path = Path(temp_dir) / "gen.ahk"
            generate_ahk(store, ahk_path, backup=False)
            imported = import_ahk(ahk_path)

        self.assertEqual(imported.expansions[0].replacement, "Be right back!")
        self.assertEqual(imported.expansions[0].notes, "quick")

    def test_unmarked_dynamic_block_is_reconstructed(self) -> None:
        # An old generated file (no source markers) must have its dynamic
        # expansions reconstructed from the generated code blocks.
        store = ExpansionStore(
            sections=["Dates", "Work"],
            expansions=[
                Expansion("Dates", ";ld", '{AHK_EXPR:FormatTime(A_Now, "yyyy-MM-dd")}'),
                Expansion("Work", ";ask", "Hi {AHK_INPUT:name|Enter name|Name|}, ok"),
            ],
        )

        with TemporaryDirectory() as temp_dir:
            ahk_path = Path(temp_dir) / "old.ahk"
            generate_ahk(store, ahk_path, backup=False)
            text = ahk_path.read_text(encoding="utf-8")
            without_markers = "\n".join(
                line for line in text.splitlines() if not line.strip().startswith("; @tem:")
            )
            ahk_path.write_text(without_markers, encoding="utf-8")
            imported = import_ahk(ahk_path)

        by_trigger = {item.trigger: item.replacement for item in imported.expansions}
        self.assertEqual(by_trigger[";ld"], '{AHK_EXPR:FormatTime(A_Now, "yyyy-MM-dd")}')
        self.assertEqual(by_trigger[";ask"], "Hi {AHK_INPUT:name|Enter name|Name|}, ok")

    def test_unrecognised_code_block_hotstring_is_skipped(self) -> None:
        # A code block that is not in the generated form cannot be reversed and
        # must be skipped rather than imported as a corrupt empty expansion.
        content = (
            "; === Misc ===\n"
            ":C:;weird::\n"
            "{\n"
            "    SomethingUnexpected()\n"
            "}\n"
        )
        with TemporaryDirectory() as temp_dir:
            ahk_path = Path(temp_dir) / "old.ahk"
            ahk_path.write_text(content, encoding="utf-8")
            imported = import_ahk(ahk_path)

        self.assertEqual(imported.expansions, [])


if __name__ == "__main__":
    unittest.main()
