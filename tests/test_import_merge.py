import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ahk_manager import (
    PLACEHOLDER_RE,
    Expansion,
    ExpansionStore,
    TemplateDef,
    VariableDef,
    _apply_renames,
    count_import_conflicts,
    generate_ahk,
    import_ahk,
    merge_imported_store,
    render_ahk,
)


class NotesRoundTripTests(unittest.TestCase):
    """A note must not be able to forge the structures import looks for.

    Notes are free text from a multiline box and are written into the script as
    comments, which import reads past. A note line that looks like a hotstring
    or a section header used to come back as one.
    """

    def _round_trip(self, notes: str) -> ExpansionStore:
        with TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "generated.ahk"
            generate_ahk(
                ExpansionStore(
                    sections=["General"],
                    expansions=[Expansion("General", ";sig", "x", True, notes)],
                ),
                script,
                backup=False,
            )
            return import_ahk(script)

    def test_a_multiline_note_survives_unchanged(self) -> None:
        for label, notes in (("LF", "one\ntwo"), ("CRLF", "one\r\ntwo")):
            with self.subTest(label):
                imported = self._round_trip(notes)

                self.assertEqual(len(imported.expansions), 1)
                self.assertEqual(imported.expansions[0].notes, notes)

    def test_a_note_line_shaped_like_a_hotstring_stays_a_note(self) -> None:
        imported = self._round_trip("one\n:CT:;evil::pwned")

        self.assertEqual([e.trigger for e in imported.expansions], [";sig"])

    def test_a_note_line_shaped_like_a_section_header_stays_a_note(self) -> None:
        imported = self._round_trip("one\n=== Injected ===")

        self.assertEqual(imported.sections, ["General"])

    def test_a_note_line_shaped_like_a_marker_stays_a_note(self) -> None:
        imported = self._round_trip('one\n@tem-var: {"name":"zzz","type":"text_input"}')

        self.assertEqual(imported.variables, [])


class MarkerValidationTests(unittest.TestCase):
    """A marker record is held to the same shape as one read from JSON.

    The markers went straight to from_dict, which coerces whatever it finds,
    while ExpansionStore.load checked the same fields first. Both routes end
    with the record merged into the live library and autosaved, so the lenient
    one decided what ended up on disk.
    """

    def _import(self, marker: str) -> ExpansionStore:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "gen.ahk"
            path.write_text(
                "#Requires AutoHotkey v2.0\n\n" + marker + "\n", encoding="utf-8"
            )
            return import_ahk(path)

    def test_a_field_of_the_wrong_type_is_refused(self) -> None:
        for label, marker in (
            (
                "@tem-skipped enabled",
                '; @tem-skipped: {"trigger":"x","replacement":"ok","enabled":"false"}',
            ),
            ("@tem-var prompt", '; @tem-var: {"name":"v","prompt_text":{"a":1}}'),
            ("@tem-var options", '; @tem-var: {"name":"v","list_options":[{"k":2}]}'),
            ("@tem-template body", '; @tem-template: {"name":"T","body":7}'),
            ("@tem replacement", '; @tem: {"replacement":["a"]}'),
        ):
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    self._import(marker)

    def test_the_message_places_the_fault(self) -> None:
        # It goes straight into the Import error dialog, so it has to say which
        # marker, which line and what was wrong.
        with self.assertRaises(ValueError) as caught:
            self._import('; @tem-var: {"name":"v","prompt_text":{"a":1}}')

        message = str(caught.exception)
        self.assertIn("gen.ahk", message)
        self.assertIn("@tem-var", message)
        self.assertIn("line 3", message)
        self.assertIn('"prompt_text"', message)
        self.assertIn("dict", message)

    def test_a_marker_that_is_not_json_is_refused(self) -> None:
        # Previously skipped in silence, which lost the definition and left the
        # expansions that referenced it undefined.
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            self._import('; @tem-var: {"name": ')

    def test_a_marker_that_is_not_an_object_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be a JSON object"):
            self._import("; @tem-var: [1, 2]")

    def test_a_well_formed_marker_still_imports(self) -> None:
        imported = self._import(
            '; @tem-var: {"name":"v","type":"text_input","prompt_text":"P"}'
        )

        self.assertEqual(imported.variables[0].name, "v")
        self.assertEqual(imported.variables[0].prompt_text, "P")

    def test_a_generated_file_round_trips_through_the_stricter_check(self) -> None:
        # The guard must not reject what the generator itself writes.
        store = ExpansionStore(
            sections=["Work"],
            expansions=[
                Expansion("Work", ";a", "{VAR:v}", False, "note"),
                Expansion("Work", ";empty", "{TPL:Empty}"),
            ],
            variables=[VariableDef("v", "text_input", "Prompt", "", [], "")],
            templates=[TemplateDef("Empty", body=""), TemplateDef("T", body="x")],
        )

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "gen.ahk"
            generate_ahk(store, path, backup=False)
            imported = import_ahk(path)

        self.assertEqual(len(imported.expansions), 2)
        self.assertEqual(len(imported.variables), 1)
        self.assertEqual(len(imported.templates), 2)


class ImportedStoreValidationTests(unittest.TestCase):
    """An import that cannot generate is refused, not merged and autosaved.

    Field types were checked but the rules the editors and the generator apply
    were not, so a brace-bearing template name, an unsupported variable type or
    a duplicate definition imported cleanly, was reported as a success, and
    broke generation later with nothing connecting it back to the import.

    Stricter than ExpansionStore.load on purpose: refusing to open the library
    already on disk would lock the user out of repairing it, while refusing an
    import only declines a file.
    """

    def _import(self, body: str) -> ExpansionStore:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "gen.ahk"
            path.write_text(
                "#Requires AutoHotkey v2.0\n\n" + body + "\n", encoding="utf-8"
            )
            return import_ahk(path)

    def test_a_definition_that_breaks_its_own_rules_is_refused(self) -> None:
        for label, marker in (
            ("brace in a template name", '; @tem-template: {"name":"Bad}Name","body":"x"}'),
            ("unsupported variable type", '; @tem-var: {"name":"v","type":"nonsense"}'),
            (
                "list variable with no options",
                '; @tem-var: {"name":"v","type":"list_selection","list_options":[]}',
            ),
            ("name that is not an identifier", '; @tem-var: {"name":"has space"}'),
        ):
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    self._import(marker)

    def test_the_message_places_the_marker(self) -> None:
        with self.assertRaises(ValueError) as caught:
            self._import('; @tem-template: {"name":"Bad}Name","body":"x"}')

        message = str(caught.exception)
        self.assertIn("gen.ahk", message)
        self.assertIn("@tem-template", message)
        self.assertIn("line 3", message)
        self.assertIn("brace", message)

    def test_duplicate_definitions_are_refused(self) -> None:
        for label, body in (
            (
                "variables",
                '; @tem-var: {"name":"v"}\n; @tem-var: {"name":"v"}',
            ),
            (
                "templates",
                '; @tem-template: {"name":"T","body":"a"}\n'
                '; @tem-template: {"name":"T","body":"b"}',
            ),
        ):
            with self.subTest(label):
                with self.assertRaisesRegex(ValueError, "Duplicate"):
                    self._import(body)

    def test_malformed_placeholder_text_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, 'trigger ";x"'):
            self._import('; @tem: {"replacement":"{VAR:}"}\n:CT:;x::text')

    def test_a_reference_the_importing_library_supplies_still_imports(self) -> None:
        # The deliberate exception. A file may legitimately use a definition
        # the target already has, so references are left to generate time --
        # resolving against the imported file alone would refuse this.
        imported = self._import('; @tem: {"replacement":"{VAR:elsewhere}"}\n:C:;x::')

        self.assertEqual(imported.expansions[0].replacement, "{VAR:elsewhere}")

    def test_a_generated_file_still_imports(self) -> None:
        # The guard must not reject what this application writes.
        store = ExpansionStore(
            sections=["Work"],
            expansions=[
                Expansion("Work", ";a", "Dear {VAR:v}{TPL:Sig}"),
                Expansion("Work", ";empty", "{TPL:Empty}"),
            ],
            variables=[
                VariableDef("v", "text_input", "Name", "", [], ""),
                VariableDef("pick", "list_selection", "Pick", "", ["a", "b"], ""),
            ],
            templates=[TemplateDef("Sig", body="Regards"), TemplateDef("Empty", body="")],
        )

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "gen.ahk"
            generate_ahk(store, path, backup=False)
            imported = import_ahk(path)

        self.assertEqual(len(imported.expansions), 2)
        self.assertEqual(len(imported.variables), 2)
        self.assertEqual(len(imported.templates), 2)

    def test_a_plain_hotstring_file_still_imports(self) -> None:
        # Nothing here defines anything, and it must stay importable.
        imported = self._import(":*:btw::by the way")

        self.assertEqual(imported.expansions[0].replacement, "by the way")


class DefinitionConflictTests(unittest.TestCase):
    """A same-name variable or template is a conflict, not a silent keep.

    Definitions were merged by name with the existing one always winning, and
    the conflict count only looked at triggers. So an import reported no
    conflict, added nothing, and left the imported expansions bound to
    definitions they did not ship with -- generating something other than the
    script they came from, with nothing said.
    """

    def _stores(self) -> tuple[ExpansionStore, ExpansionStore]:
        target = ExpansionStore(
            sections=["Work"],
            expansions=[Expansion("Work", ";old", "uses {TPL:Sig} and {VAR:v}")],
            variables=[VariableDef("v", "text_input", "EXISTING", "", [], "")],
            templates=[TemplateDef("Sig", body="EXISTING")],
        )
        imported = ExpansionStore(
            sections=["Work"],
            expansions=[Expansion("Work", ";new", "uses {TPL:Sig} and {VAR:v}")],
            variables=[VariableDef("v", "text_input", "IMPORTED", "", [], "")],
            templates=[TemplateDef("Sig", body="IMPORTED")],
        )
        return target, imported

    def test_a_differing_definition_counts_as_a_conflict(self) -> None:
        target, imported = self._stores()

        conflicts = count_import_conflicts(target, imported)

        self.assertEqual(conflicts.triggers, 0)
        self.assertEqual(conflicts.definitions, 2)
        self.assertTrue(conflicts)

    def test_a_matching_definition_is_not_a_conflict(self) -> None:
        # Re-importing a file generated from this same library collides on
        # every name and differs in none, and must not raise the question.
        target, imported = self._stores()
        imported.variables[0].prompt_text = "EXISTING"
        imported.templates[0].body = "EXISTING"

        conflicts = count_import_conflicts(target, imported)

        self.assertEqual(conflicts.definitions, 0)
        self.assertFalse(conflicts)

    def test_skip_keeps_the_definitions_already_here(self) -> None:
        target, imported = self._stores()

        result = merge_imported_store(target, imported, "skip")

        self.assertEqual(target.templates[0].body, "EXISTING")
        self.assertEqual(target.variables[0].prompt_text, "EXISTING")
        self.assertEqual(result.definitions_skipped, 2)

    def test_overwrite_replaces_them(self) -> None:
        target, imported = self._stores()

        result = merge_imported_store(target, imported, "overwrite")

        self.assertEqual(target.templates[0].body, "IMPORTED")
        self.assertEqual(target.variables[0].prompt_text, "IMPORTED")
        self.assertEqual(result.definitions_overwritten, 2)

    def test_overwrite_also_changes_the_expansions_already_here(self) -> None:
        # The consequence the dialog has to spell out: this reaches expansions
        # that were never part of the import.
        target, imported = self._stores()

        merge_imported_store(target, imported, "overwrite")

        self.assertIn("IMPORTED", render_ahk(target))

    def test_rename_keeps_both_and_repoints_the_imported_expansions(self) -> None:
        # Renaming the definition alone would leave the imported expansion
        # saying {TPL:Sig}, so it would bind to the copy already here -- the
        # exact behaviour the choice exists to avoid.
        target, imported = self._stores()

        result = merge_imported_store(target, imported, "rename")

        by_trigger = {e.trigger: e.replacement for e in target.expansions}
        self.assertEqual(by_trigger[";old"], "uses {TPL:Sig} and {VAR:v}")
        self.assertEqual(
            by_trigger[";new"], "uses {TPL:Sig_imported} and {VAR:v_imported}"
        )
        self.assertEqual(result.definitions_renamed, 2)
        self.assertEqual([t.name for t in target.templates], ["Sig", "Sig_imported"])
        self.assertEqual([v.name for v in target.variables], ["v", "v_imported"])

    def test_rename_leaves_a_library_that_still_generates(self) -> None:
        target, imported = self._stores()

        merge_imported_store(target, imported, "rename")

        output = render_ahk(target)
        self.assertIn("EXISTING", output)
        self.assertIn("IMPORTED", output)

    def test_rename_rewrites_bodies_of_definitions_it_only_adds(self) -> None:
        # A template with no name clash still has to follow a renamed variable.
        target, imported = self._stores()
        imported.templates.append(TemplateDef("Fresh", body="new {VAR:v}"))

        merge_imported_store(target, imported, "rename")

        fresh = next(t for t in target.templates if t.name == "Fresh")
        self.assertEqual(fresh.body, "new {VAR:v_imported}")

    def test_rename_renames_a_matching_definition_too(self) -> None:
        # Matching contents decide whether to ask, not what the answer does:
        # keeping both means the imported expansions keep their own copy, which
        # stays true after either copy is edited.
        target, imported = self._stores()
        imported.templates[0].body = "EXISTING"

        merge_imported_store(target, imported, "rename")

        self.assertEqual([t.name for t in target.templates], ["Sig", "Sig_imported"])

    def test_renaming_does_not_disturb_the_imported_store(self) -> None:
        target, imported = self._stores()

        merge_imported_store(target, imported, "rename")

        self.assertEqual(imported.expansions[0].replacement, "uses {TPL:Sig} and {VAR:v}")
        self.assertEqual(imported.templates[0].name, "Sig")

    def test_a_second_rename_does_not_collide(self) -> None:
        target, imported = self._stores()
        target.templates.append(TemplateDef("Sig_imported", body="ALREADY TAKEN"))

        merge_imported_store(target, imported, "rename")

        self.assertEqual(
            [t.name for t in target.templates], ["Sig", "Sig_imported", "Sig_imported2"]
        )

    def test_a_generated_name_does_not_land_on_an_imported_one(self) -> None:
        # The imported store already had a "v_imported", so allocating that
        # name for its "v" merged the two: both references ended up on one
        # definition and the other renamed copy was orphaned.
        target = ExpansionStore(
            sections=["Work"],
            variables=[VariableDef("v", "text_input", "TARGET", "", [], "")],
        )
        imported = ExpansionStore(
            sections=["Work"],
            expansions=[Expansion("Work", ";new", "{VAR:v}/{VAR:v_imported}")],
            variables=[
                VariableDef("v", "text_input", "IMPORTED v", "", [], ""),
                VariableDef("v_imported", "text_input", "IMPORTED v_imported", "", [], ""),
            ],
        )

        merge_imported_store(target, imported, "rename")

        self.assertEqual(
            target.expansions[0].replacement, "{VAR:v_imported2}/{VAR:v_imported}"
        )
        self.assertEqual(
            [v.name for v in target.variables], ["v", "v_imported2", "v_imported"]
        )

    def test_the_same_holds_for_templates(self) -> None:
        target = ExpansionStore(
            sections=["Work"], templates=[TemplateDef("Sig", body="TARGET")]
        )
        imported = ExpansionStore(
            sections=["Work"],
            expansions=[Expansion("Work", ";new", "{TPL:Sig}/{TPL:Sig_imported}")],
            templates=[
                TemplateDef("Sig", body="IMPORTED Sig"),
                TemplateDef("Sig_imported", body="IMPORTED Sig_imported"),
            ],
        )

        merge_imported_store(target, imported, "rename")

        self.assertEqual(
            target.expansions[0].replacement, "{TPL:Sig_imported2}/{TPL:Sig_imported}"
        )
        output = render_ahk(target)
        self.assertIn("IMPORTED Sig", output)
        self.assertIn("IMPORTED Sig_imported", output)

    def test_no_renamed_definition_is_left_unreferenced(self) -> None:
        # What "keep both" means: each imported definition still answers to
        # whatever imported it.
        target = ExpansionStore(
            sections=["Work"],
            variables=[VariableDef("v", "text_input", "TARGET", "", [], "")],
        )
        imported = ExpansionStore(
            sections=["Work"],
            expansions=[Expansion("Work", ";new", "{VAR:v}/{VAR:v_imported}")],
            variables=[
                VariableDef("v", "text_input", "IMPORTED v", "", [], ""),
                VariableDef("v_imported", "text_input", "IMPORTED v_imported", "", [], ""),
            ],
        )

        merge_imported_store(target, imported, "rename")

        referenced = {
            match.group(2)
            for expansion in target.expansions
            for match in PLACEHOLDER_RE.finditer(expansion.replacement)
        }
        # "v" is the target's own and was never referenced here; everything the
        # import brought must be.
        self.assertEqual({v.name for v in target.variables} - referenced, {"v"})

    def test_a_definition_that_does_not_collide_is_not_renamed(self) -> None:
        # Only a clash with the target earns a new name. Renaming anything else
        # is what let one generated name chain into another.
        target = ExpansionStore(
            sections=["Work"],
            variables=[VariableDef("v", "text_input", "TARGET", "", [], "")],
        )
        imported = ExpansionStore(
            sections=["Work"],
            expansions=[Expansion("Work", ";new", "{VAR:other}")],
            variables=[VariableDef("other", "text_input", "P", "", [], "")],
        )

        merge_imported_store(target, imported, "rename")

        self.assertEqual(target.expansions[0].replacement, "{VAR:other}")

    def test_references_are_rewritten_in_one_pass(self) -> None:
        # Applied one mapping at a time, an earlier substitution's output is
        # still there for a later one to match.
        self.assertEqual(
            _apply_renames("{VAR:a}/{VAR:b}", {("VAR", "a"): "b", ("VAR", "b"): "c"}),
            "{VAR:b}/{VAR:c}",
        )

    def test_a_definition_with_no_counterpart_is_still_just_added(self) -> None:
        target, imported = self._stores()
        imported.variables.append(VariableDef("brand_new", "text_input", "P", "", [], ""))

        result = merge_imported_store(target, imported, "skip")

        self.assertEqual(result.variables_added, 1)
        self.assertIn("brand_new", [v.name for v in target.variables])


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

    def test_unmarked_select_block_round_trips_without_window_title(self) -> None:
        # TEM_Select carries the branded window title after its options array.
        # Reconstruction must drop it rather than read it back as an option.
        store = ExpansionStore(
            sections=["Status"],
            expansions=[
                Expansion("Status", ";st", "Status: {AHK_SELECT:state|Pick one|State|Open||Closed}"),
            ],
        )

        with TemporaryDirectory() as temp_dir:
            ahk_path = Path(temp_dir) / "gen.ahk"
            generate_ahk(store, ahk_path, backup=False)
            text = ahk_path.read_text(encoding="utf-8")
            self.assertIn('"Text Expansion Manager - `;st"', text)
            without_markers = "\n".join(
                line for line in text.splitlines() if not line.strip().startswith("; @tem:")
            )
            ahk_path.write_text(without_markers, encoding="utf-8")
            imported = import_ahk(ahk_path)

        self.assertEqual(
            imported.expansions[0].replacement,
            "Status: {AHK_SELECT:state|Pick one|State|Open||Closed}",
        )

    def test_semicolons_survive_an_unmarked_round_trip(self) -> None:
        # Generation escapes every semicolon as `; so it cannot open a comment.
        # Reconstruction from the code block has to reverse that.
        store = ExpansionStore(
            sections=["Work", "Status"],
            expansions=[
                Expansion("Work", ";note", "See ; footnote {AHK_INPUT:n|Num ; here|Num|}"),
                Expansion("Status", ";st", "{AHK_SELECT:state|Pick ; one|State|Open ; now||Closed}"),
            ],
        )

        with TemporaryDirectory() as temp_dir:
            ahk_path = Path(temp_dir) / "gen.ahk"
            generate_ahk(store, ahk_path, backup=False)
            text = ahk_path.read_text(encoding="utf-8")
            without_markers = "\n".join(
                line for line in text.splitlines() if not line.strip().startswith("; @tem:")
            )
            ahk_path.write_text(without_markers, encoding="utf-8")
            imported = import_ahk(ahk_path)

        by_trigger = {item.trigger: item.replacement for item in imported.expansions}
        self.assertEqual(
            by_trigger[";note"], "See ; footnote {AHK_INPUT:n|Num ; here|Num|}"
        )
        self.assertEqual(
            by_trigger[";st"],
            "{AHK_SELECT:state|Pick ; one|State|Open ; now||Closed}",
        )

    def test_key_ending_expansion_round_trips_without_the_end_char_trailer(self) -> None:
        # Reconstruction skips the ending-character trailer when it finds one;
        # an expansion ending in a key no longer emits it at all.
        store = ExpansionStore(
            sections=["Keys"],
            expansions=[Expansion("Keys", ";next", "Value{AHK_KEY:Tab}")],
        )

        with TemporaryDirectory() as temp_dir:
            ahk_path = Path(temp_dir) / "gen.ahk"
            generate_ahk(store, ahk_path, backup=False)
            text = ahk_path.read_text(encoding="utf-8")
            without_markers = "\n".join(
                line for line in text.splitlines() if not line.strip().startswith("; @tem:")
            )
            ahk_path.write_text(without_markers, encoding="utf-8")
            imported = import_ahk(ahk_path)

        self.assertEqual(imported.expansions[0].replacement, "Value{AHK_KEY:Tab}")

    def _without_markers(self, store: ExpansionStore) -> ExpansionStore:
        """Generate, strip the @tem markers, and import what is left.

        The markers are authoritative for any file this app wrote, so this is
        what exercises _reconstruct_replacement -- the path that has to read the
        generated code itself.
        """
        with TemporaryDirectory() as temp_dir:
            ahk_path = Path(temp_dir) / "gen.ahk"
            generate_ahk(store, ahk_path, backup=False)
            text = ahk_path.read_text(encoding="utf-8")
            ahk_path.write_text(
                "\n".join(
                    line
                    for line in text.splitlines()
                    if not line.strip().startswith("; @tem:")
                ),
                encoding="utf-8",
            )
            return import_ahk(ahk_path)

    def test_form_fields_read_from_the_map_round_trip(self) -> None:
        # The answers are no longer copied into locals, so reconstruction reads
        # the placeholder off the map lookup instead of a bare name.
        store = ExpansionStore(
            sections=["Letters"],
            expansions=[
                Expansion("Letters", ";dear", "Dear {AHK_INPUT:who|Name|Name|}, hello")
            ],
        )

        imported = self._without_markers(store)

        self.assertEqual(
            imported.expansions[0].replacement,
            "Dear {AHK_INPUT:who|Name|Name|}, hello",
        )

    def test_a_repeated_field_round_trips_as_two_placeholders(self) -> None:
        store = ExpansionStore(
            sections=["Letters"],
            expansions=[
                Expansion("Letters", ";t", "{AHK_INPUT:n|N|N|} and {AHK_INPUT:n|N|N|}")
            ],
        )

        imported = self._without_markers(store)

        self.assertEqual(
            imported.expansions[0].replacement,
            "{AHK_INPUT:n|N|N|} and {AHK_INPUT:n|N|N|}",
        )

    def test_a_select_read_from_its_own_local_round_trips(self) -> None:
        store = ExpansionStore(
            sections=["Status"],
            expansions=[
                Expansion("Status", ";st", "Status: {AHK_SELECT:s|Pick|State|Open||Shut}")
            ],
        )

        imported = self._without_markers(store)

        self.assertEqual(
            imported.expansions[0].replacement,
            "Status: {AHK_SELECT:s|Pick|State|Open||Shut}",
        )

    def test_legacy_form_assignments_still_import(self) -> None:
        # Scripts generated while each answer was copied into a local named by
        # the user. Those files are still on disk, so both shapes have to read.
        legacy = "\n".join(
            [
                "#Requires AutoHotkey v2.0",
                "",
                "; === Letters ===",
                ":C:;dear::",
                "{",
                '    __tem_result := ""',
                '    __tem_fields := [Map("name", "who", "label", "Name", '
                '"title", "Name", "kind", "input", "default", "")]',
                '    __tem_parts := ["Dear ", Map("var", "who")]',
                '    __tem_vals := TEM_Form("t", __tem_fields, __tem_parts)',
                "    if (!IsObject(__tem_vals))",
                "        return",
                '    who := __tem_vals["who"]',
                '    __tem_result .= "Dear "',
                "    __tem_result .= who",
                '    if (__tem_result != "") {',
                "        SendText(__tem_result)",
                '        __tem_result := ""',
                "    }",
                "}",
                "",
            ]
        )
        with TemporaryDirectory() as temp_dir:
            ahk_path = Path(temp_dir) / "legacy.ahk"
            ahk_path.write_text(legacy, encoding="utf-8")
            imported = import_ahk(ahk_path)

        self.assertEqual(
            imported.expansions[0].replacement, "Dear {AHK_INPUT:who|Name|Name|}"
        )

    def _round_trip(self, store: ExpansionStore) -> ExpansionStore:
        with TemporaryDirectory() as temp_dir:
            ahk_path = Path(temp_dir) / "gen.ahk"
            generate_ahk(store, ahk_path, backup=False)
            return import_ahk(ahk_path)

    def _empty_template_store(self) -> ExpansionStore:
        # A template created but not yet written is an easy state to reach, and
        # an expansion referencing it generates no hotstring at all.
        return ExpansionStore(
            sections=["Work"],
            expansions=[
                Expansion("Work", ";empty", "{TPL:Empty}", True, "keep me"),
                Expansion("Work", ";next", "works"),
            ],
            templates=[TemplateDef("Empty", body="")],
        )

    def test_an_expansion_that_generates_nothing_still_round_trips(self) -> None:
        # It used to emit only a comment, so there was nothing for import to
        # find and the expansion was silently gone.
        imported = self._round_trip(self._empty_template_store())

        self.assertEqual([e.trigger for e in imported.expansions], [";empty", ";next"])

    def test_the_skipped_record_keeps_everything_about_it(self) -> None:
        imported = self._round_trip(self._empty_template_store())
        skipped = imported.expansions[0]

        self.assertEqual(skipped.replacement, "{TPL:Empty}")
        self.assertEqual(skipped.section, "Work")
        self.assertEqual(skipped.notes, "keep me")
        self.assertTrue(skipped.enabled)

    def test_a_disabled_skipped_expansion_stays_disabled(self) -> None:
        # There is no hotstring line for the enabled state to be read off, so
        # the record has to carry it.
        store = self._empty_template_store()
        store.expansions[0].enabled = False

        self.assertFalse(self._round_trip(store).expansions[0].enabled)

    def test_a_blank_replacement_round_trips(self) -> None:
        store = ExpansionStore(
            sections=["Work"], expansions=[Expansion("Work", ";blank", "")]
        )

        imported = self._round_trip(store)

        self.assertEqual([e.trigger for e in imported.expansions], [";blank"])
        self.assertEqual(imported.expansions[0].replacement, "")

    def test_a_nested_template_resolving_to_nothing_round_trips(self) -> None:
        store = ExpansionStore(
            sections=["Work"],
            expansions=[Expansion("Work", ";n", "{TPL:Outer}")],
            templates=[
                TemplateDef("Empty", body=""),
                TemplateDef("Outer", body="{TPL:Empty}"),
            ],
        )

        imported = self._round_trip(store)

        self.assertEqual(imported.expansions[0].replacement, "{TPL:Outer}")

    def test_a_skipped_expansion_keeps_its_own_section(self) -> None:
        # No "; === ... ===" header precedes the record, so the section has to
        # come from the record itself.
        store = ExpansionStore(
            sections=["First", "Second"],
            expansions=[
                Expansion("First", ";a", "text"),
                Expansion("Second", ";empty", "{TPL:Empty}"),
            ],
            templates=[TemplateDef("Empty", body="")],
        )

        imported = self._round_trip(store)
        by_trigger = {e.trigger: e.section for e in imported.expansions}

        self.assertEqual(by_trigger[";empty"], "Second")

    def test_legacy_three_argument_select_still_imports(self) -> None:
        # Scripts generated before the window title was added have no fourth
        # argument, and must keep importing with their options intact.
        legacy = "\n".join(
            [
                "#Requires AutoHotkey v2.0",
                "",
                "; === Status ===",
                ":C:;st::",
                "{",
                '    __tem_result := ""',
                '    __tem_result .= "Status: "',
                '    __tem_select_state := TEM_Select("Pick one", "State", ["Open", "Closed"])',
                "    if (!__tem_select_state.ok)",
                "        return",
                "    state := __tem_select_state.value",
                "    __tem_result .= state",
                '    if (__tem_result != "") {',
                "        SendText(__tem_result)",
                '        __tem_result := ""',
                "    }",
                "}",
                "",
            ]
        )

        with TemporaryDirectory() as temp_dir:
            ahk_path = Path(temp_dir) / "legacy.ahk"
            ahk_path.write_text(legacy, encoding="utf-8")
            imported = import_ahk(ahk_path)

        self.assertEqual(
            imported.expansions[0].replacement,
            "Status: {AHK_SELECT:state|Pick one|State|Open||Closed}",
        )

    def test_unmarked_form_block_round_trips_every_field(self) -> None:
        # The form gathers all prompts in one dialog, so an unmarked block must
        # be reversed from its fields array: a select's options must survive,
        # and a variable used twice must come back as two placeholders even
        # though the form declares it once.
        replacement = (
            "{AHK_INPUT:client|Client name|Client|Acme} owes "
            "{AHK_SELECT:kind|Kind|Kind|ACH||Wire} "
            "of ${AHK_INPUT:amount|Amount|Amount|} on {AHK_INPUT:client|Client name|Client|Acme}"
        )
        store = ExpansionStore(
            sections=["Work"],
            expansions=[Expansion("Work", ";owe", replacement)],
        )

        with TemporaryDirectory() as temp_dir:
            ahk_path = Path(temp_dir) / "old.ahk"
            generate_ahk(store, ahk_path, backup=False)
            text = ahk_path.read_text(encoding="utf-8")
            ahk_path.write_text(
                "\n".join(
                    line
                    for line in text.splitlines()
                    if not line.strip().startswith("; @tem:")
                ),
                encoding="utf-8",
            )
            imported = import_ahk(ahk_path)

        self.assertEqual(imported.expansions[0].replacement, replacement)

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
