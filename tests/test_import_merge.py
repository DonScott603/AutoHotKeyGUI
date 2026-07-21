import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ahk_manager import (
    Expansion,
    ExpansionStore,
    TemplateDef,
    VariableDef,
    generate_ahk,
    import_ahk,
    merge_imported_store,
    render_ahk,
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

    def test_variables_and_templates_round_trip_through_ahk(self) -> None:
        store = ExpansionStore(
            sections=["General"],
            expansions=[
                Expansion("General", "hi", "Hi {{VAR:name}} from [[TEMPLATE:sig]]"),
            ],
            variables=[
                VariableDef(
                    name="name",
                    type="list_selection",
                    prompt_text="Your name?",
                    default_value="Scott",
                    list_options=["Scott", "Sam"],
                    notes="who",
                ),
            ],
            templates=[
                TemplateDef(name="sig", description="signature", body="Best, Scott"),
            ],
        )

        imported = self._render_and_import(store)

        self.assertEqual(len(imported.variables), 1)
        self.assertEqual(len(imported.templates), 1)
        self.assertEqual(imported.variables[0], store.variables[0])
        self.assertEqual(imported.templates[0], store.templates[0])

    def test_imported_variables_and_templates_are_merged_by_name(self) -> None:
        imported = ExpansionStore(
            sections=["General"],
            variables=[
                VariableDef(name="name", type="text_input"),
                VariableDef(name="city", type="text_input"),
            ],
            templates=[TemplateDef(name="sig", body="new body")],
        )
        target = ExpansionStore(
            sections=["General"],
            variables=[VariableDef(name="name", type="text_input", notes="keep me")],
            templates=[TemplateDef(name="sig", body="existing body")],
        )

        result = merge_imported_store(target, imported)

        self.assertEqual(result.variables_added, 1)
        self.assertEqual(result.templates_added, 0)
        self.assertEqual([v.name for v in target.variables], ["name", "city"])
        # Existing same-name definitions are left untouched.
        self.assertEqual(target.variables[0].notes, "keep me")
        self.assertEqual(target.templates[0].body, "existing body")

    def _render_and_import(self, store: ExpansionStore) -> ExpansionStore:
        with TemporaryDirectory() as temp_dir:
            ahk_path = Path(temp_dir) / "out.ahk"
            ahk_path.write_text(render_ahk(store), encoding="utf-8")
            return import_ahk(ahk_path)


if __name__ == "__main__":
    unittest.main()
