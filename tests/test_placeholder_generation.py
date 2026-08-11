import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ahk_manager import (
    AHK_CONFIG_DIR_NAME,
    AHK_ICON_NAME,
    AHK_THEME_COLORS,
    Expansion,
    ExpansionStore,
    TemplateDef,
    TemplatePlaceholder,
    generate_ahk,
    import_ahk,
    parse_replacement_template,
    render_ahk,
)


class PlaceholderGenerationTests(unittest.TestCase):
    def test_literal_expansion_stays_one_line(self) -> None:
        store = ExpansionStore(
            sections=["Common"],
            expansions=[Expansion("Common", "brb", "Be right back")],
        )

        output = render_ahk(store)

        self.assertIn(":CT:brb::Be right back", output)
        self.assertNotIn("SendText(__tem_result)", output)

    def _rendered(self, replacement: str, templates: list[TemplateDef] | None = None) -> str:
        return render_ahk(
            ExpansionStore(
                sections=["Common"],
                expansions=[Expansion("Common", "sig", replacement)],
                templates=templates or [],
            )
        )

    def test_a_line_break_in_literal_text_survives(self) -> None:
        # A static hotstring ends at its own line break, so this text used to be
        # folded to "first second" with nothing said about it.
        output = self._rendered("first\nsecond")

        self.assertIn('SendText("first`nsecond")', output)
        self.assertNotIn("first second", output)

    def test_a_crlf_line_break_survives(self) -> None:
        self.assertIn('SendText("first`r`nsecond")', self._rendered("first\r\nsecond"))

    def test_a_template_supplied_line_break_survives(self) -> None:
        output = self._rendered("{TPL:Signoff}", [TemplateDef("Signoff", body="Regards,\nDon")])

        self.assertIn('SendText("Regards,`nDon")', output)

    def test_multiline_text_needing_paste_still_pastes(self) -> None:
        # The dash rule picks the delivery method; the line break only decides
        # that this cannot be a static hotstring.
        output = self._rendered("A -- B\nnext")

        self.assertIn('TEM_Paste("A -- B`nnext")', output)
        self.assertNotIn("SendText(\"A", output)

    def test_single_line_text_is_still_a_static_hotstring(self) -> None:
        # The block form costs a helper and a code path, so text that does not
        # need it must not get it.
        output = self._rendered("Regards, Don")

        self.assertIn(":CT:sig::Regards, Don", output)
        self.assertNotIn("SendText", output)

    def test_the_source_marker_keeps_the_original_line_breaks(self) -> None:
        # The marker is what import reads back, so the stored replacement has
        # to survive the trip unchanged.
        self.assertIn(r'"replacement":"first\nsecond"', self._rendered("first\nsecond"))

    def _with_notes(self, notes: str, enabled: bool = True, replacement: str = "x") -> str:
        return render_ahk(
            ExpansionStore(
                sections=["Common"],
                expansions=[Expansion("Common", "sig", replacement, enabled, notes)],
            )
        )

    def test_every_line_of_a_multiline_note_is_commented(self) -> None:
        # Only the first line used to get a marker; the rest were written at
        # column zero, where AutoHotkey parses them as code.
        output = self._with_notes("Used for clients\nUpdated July 2026")

        self.assertIn("; Notes: Used for clients", output)
        self.assertIn("; Notes: Updated July 2026", output)
        for line in output.splitlines():
            if "Updated July 2026" in line:
                self.assertTrue(line.startswith(";"), line)

    def test_a_note_cannot_introduce_a_statement(self) -> None:
        output = self._with_notes('one\nMsgBox("unexpected")')

        self.assertNotIn('\nMsgBox("unexpected")', output)

    def test_a_note_cannot_introduce_a_second_hotstring(self) -> None:
        # This one is the quiet failure: it is valid AutoHotkey, so the script
        # loads and the extra trigger is simply live.
        output = self._with_notes("one\n:CT:;evil::pwned")

        self.assertNotIn("\n:CT:;evil::pwned", output)

    def test_a_disabled_expansion_comments_every_note_line_too(self) -> None:
        # _maybe_disable_lines prefixes each list element and cannot see inside
        # one, so an embedded newline escaped it as well.
        output = self._with_notes("one\nMsgBox(1)", enabled=False)

        for line in output.splitlines():
            if "MsgBox(1)" in line:
                self.assertTrue(line.startswith(";"), line)

    def test_notes_on_a_dynamic_expansion_are_commented_per_line(self) -> None:
        output = self._with_notes("one\nMsgBox(1)", replacement="hi {AHK_INPUT:n|Name|T|}")

        self.assertIn("; Notes: MsgBox(1)", output)
        self.assertNotIn("\nMsgBox(1)", output)

    def test_the_label_repeats_rather_than_aligning(self) -> None:
        # A comment marker followed by only whitespace is the prefix
        # HOTSTRING_RE reads as a disabled hotstring, so aligned continuations
        # would be safe from AutoHotkey but not from our own importer.
        output = self._with_notes("one\ntwo")

        self.assertNotIn(";        two", output)
        self.assertEqual(output.count("; Notes: "), 2)

    def test_a_single_line_note_is_unchanged(self) -> None:
        self.assertIn("; Notes: just one", self._with_notes("just one"))

    def test_the_source_marker_still_carries_the_whole_note(self) -> None:
        # The marker is what import reads back, so the line breaks have to
        # survive there even though the comment splits them.
        self.assertIn(r'"notes":"one\ntwo"', self._with_notes("one\ntwo"))

    def test_empty_replacement_is_skipped_not_broken(self) -> None:
        # A ":opts:trigger::" line with nothing after "::" makes AutoHotkey expect
        # a code block and error. An empty replacement must instead be skipped.
        store = ExpansionStore(
            sections=["S"],
            expansions=[Expansion("S", ";x", ""), Expansion("S", "ok", "fine")],
        )

        output = render_ahk(store)

        self.assertIn('Skipped ";x": empty replacement.', output)
        self.assertIn(":CT:ok::fine", output)
        for line in output.splitlines():
            self.assertFalse(line.rstrip().endswith("::"), f"bare hotstring: {line!r}")

    def test_static_replacement_with_send_special_chars_uses_text_mode(self) -> None:
        # "!", "^", "+", "#", "{", "}" are Send modifiers/keys in AutoHotkey's
        # default hotstring mode; the "T" (Text) option sends them literally so a
        # leading/trailing "!" (or "+50% ^power {done}") is not corrupted.
        store = ExpansionStore(
            sections=["Common"],
            expansions=[Expansion("Common", "excited", "!Hello world!")],
        )

        output = render_ahk(store)

        self.assertIn(":CT:excited::!Hello world!", output)

    def test_date_time_expression_placeholder_generates_dynamic_hotstring(self) -> None:
        store = ExpansionStore(
            sections=["Dates"],
            expansions=[
                Expansion(
                    "Dates",
                    "today",
                    'Today is {AHK_EXPR:FormatTime(A_Now, "yyyy-MM-dd")}',
                )
            ],
        )

        output = render_ahk(store)

        self.assertIn(":C:today::\n{", output)
        self.assertIn('__tem_result .= "Today is "', output)
        self.assertIn('__tem_result .= FormatTime(A_Now, "yyyy-MM-dd")', output)
        self.assertIn("SendText(__tem_result)", output)

    def test_input_placeholder_generates_form_logic(self) -> None:
        store = ExpansionStore(
            sections=["Letters"],
            expansions=[
                Expansion(
                    "Letters",
                    "dear",
                    "Dear {AHK_INPUT:client_name|Enter client name|Client Name|},",
                )
            ],
        )

        output = render_ahk(store)

        self.assertIn("TEM_Form(title, fields, parts)", output)
        self.assertIn(
            'Map("name", "client_name", "label", "Enter client name", '
            '"title", "Client Name", "kind", "input", "default", "")',
            output,
        )
        self.assertIn(
            '__tem_vals := TEM_Form("Text Expansion Manager - dear", '
            "__tem_fields, __tem_parts)",
            output,
        )
        # Read from the values map rather than copied into a local named by
        # the user, which is what used to collide with built-ins and functions.
        self.assertIn('__tem_result .= __tem_vals["client_name"]', output)
        self.assertNotIn('client_name := __tem_vals', output)
        self.assertNotIn("InputBox(", output)

    def test_form_preview_parts_carry_literals_and_field_references(self) -> None:
        # The preview the dialog renders is assembled from these parts, so the
        # literals and field references must appear in reading order.
        store = ExpansionStore(
            sections=["Letters"],
            expansions=[
                Expansion(
                    "Letters",
                    "dear",
                    "Dear {AHK_INPUT:client_name|Enter client name|Client Name|},",
                )
            ],
        )

        output = render_ahk(store)

        self.assertIn(
            '__tem_parts := ["Dear ", Map("var", "client_name"), ","]',
            output,
        )

    def test_repeated_variable_becomes_one_shared_form_field(self) -> None:
        # One field, asked once, feeding both occurrences.
        store = ExpansionStore(
            sections=["Work"],
            expansions=[
                Expansion(
                    "Work",
                    "twice",
                    "{AHK_INPUT:amount|Amount|Amount|} and again {AHK_INPUT:amount|Amount|Amount|}",
                )
            ],
        )

        output = render_ahk(store)

        self.assertEqual(output.count('Map("name", "amount"'), 1)
        self.assertEqual(output.count('__tem_result .= __tem_vals["amount"]'), 2)

    def test_prompts_are_positioned_on_the_typed_on_monitor(self) -> None:
        # Both prompts anchor to where the trigger was typed. The shared helper
        # is emitted once even when both dialogs are in play, and neither may
        # fall back to a coordinate-less Show().
        store = ExpansionStore(
            sections=["Work"],
            expansions=[
                Expansion("Work", "one", "{AHK_INPUT:a|A|A|}"),
                Expansion("Work", "two", "{AHK_SELECT:b|B|B|x||y}"),
            ],
        )

        output = render_ahk(store)

        self.assertEqual(output.count("TEM_TargetPoint() {"), 1)
        # Screen coordinates: the default Client mode reports the caret relative
        # to the typed-in window and would pick the wrong monitor.
        self.assertIn('CoordMode("Caret", "Screen")', output)
        self.assertIn("TEM_ShowAt(formGui, point)", output)
        self.assertIn("TEM_ShowAt(selectGui, point)", output)
        self.assertNotIn("formGui.Show()", output)
        self.assertNotIn("selectGui.Show()", output)

    def test_multiple_selects_use_the_form_rather_than_separate_popups(self) -> None:
        # The lightweight TEM_Select popup is kept only for a lone dropdown.
        store = ExpansionStore(
            sections=["Work"],
            expansions=[
                Expansion(
                    "Work",
                    "two",
                    "{AHK_SELECT:a|A|A|x||y} {AHK_SELECT:b|B|B|p||q}",
                )
            ],
        )

        output = render_ahk(store)

        self.assertIn("TEM_Form(title, fields, parts)", output)
        self.assertNotIn("TEM_Select(", output)
        self.assertIn(
            'Map("name", "a", "label", "A", "title", "A", "kind", "select", "options", ["x", "y"])',
            output,
        )

    def test_select_placeholder_generates_selection_helper_and_logic(self) -> None:
        store = ExpansionStore(
            sections=["Status"],
            expansions=[
                Expansion(
                    "Status",
                    "status",
                    "Status: {AHK_SELECT:status|Choose status|Status|Pending||Approved||Rejected}",
                )
            ],
        )

        output = render_ahk(store)

        self.assertIn('TEM_Select(prompt, title, options, winTitle := "")', output)
        self.assertIn(
            '__tem_select_status := TEM_Select("Choose status", "Status", '
            '["Pending", "Approved", "Rejected"], "Text Expansion Manager - status")',
            output,
        )
        # The prefixed local is read directly; the answer is never copied to
        # a local named "status".
        self.assertIn("__tem_result .= __tem_select_status.value", output)
        self.assertNotIn("status := __tem_select_status.value", output)

    def test_malformed_placeholder_raises_clear_error(self) -> None:
        store = ExpansionStore(
            sections=["Bad"],
            expansions=[Expansion("Bad", "bad", "{AHK_INPUT:name|Prompt only}")],
        )

        with self.assertRaisesRegex(ValueError, "AHK_INPUT must use"):
            render_ahk(store)

    def test_tab_placeholder_parses(self) -> None:
        segments = parse_replacement_template("A{AHK_KEY:Tab}B")

        # Segments are literals or placeholders; assert which before reading it.
        placeholder = segments[1]
        assert isinstance(placeholder, TemplatePlaceholder)
        self.assertEqual(placeholder.kind, "AHK_KEY")
        self.assertEqual(placeholder.value, "Tab")

    def test_tab_placeholder_generates_dynamic_hotstring(self) -> None:
        store = ExpansionStore(
            sections=["Keys"],
            expansions=[Expansion("Keys", "tabbed", "Left{AHK_KEY:Tab}Right")],
        )

        output = render_ahk(store)

        self.assertIn(":C:tabbed::\n{", output)
        self.assertIn('SendEvent("{Tab}")', output)
        self.assertIn("Sleep(100)", output)
        self.assertIn('__tem_result .= "Left"', output)
        self.assertIn('__tem_result .= "Right"', output)
        self.assertNotIn("::tabbed::Left", output)

    def test_output_declares_single_instance_force(self) -> None:
        store = ExpansionStore(
            sections=["Common"],
            expansions=[Expansion("Common", "brb", "Be right back")],
        )

        output = render_ahk(store)

        self.assertIn("#SingleInstance Force", output)

    def test_dynamic_hotstring_reproduces_ending_character(self) -> None:
        store = ExpansionStore(
            sections=["Dates"],
            expansions=[
                Expansion(
                    "Dates",
                    "today",
                    'Today is {AHK_EXPR:FormatTime(A_Now, "yyyy-MM-dd")}',
                )
            ],
        )

        output = render_ahk(store)

        self.assertIn('if (A_EndChar = "`r" || A_EndChar = "`n") {', output)
        self.assertIn('Send("{Enter}")', output)
        self.assertIn("SendText(A_EndChar)", output)

    def test_expansion_ending_in_a_key_drops_the_ending_character(self) -> None:
        # The Tab has moved the caret to the next field by the time the ending
        # character would be replayed, so it would be typed there instead.
        store = ExpansionStore(
            sections=["Keys"],
            expansions=[Expansion("Keys", "next", "Value{AHK_KEY:Tab}")],
        )

        output = render_ahk(store)

        self.assertIn('SendEvent("{Tab}")', output)
        self.assertNotIn("A_EndChar", output)

    def test_a_key_before_more_text_keeps_the_ending_character(self) -> None:
        store = ExpansionStore(
            sections=["Keys"],
            expansions=[Expansion("Keys", "tabbed", "Left{AHK_KEY:Tab}Right")],
        )

        output = render_ahk(store)

        self.assertIn("SendText(A_EndChar)", output)

    def test_a_trailing_empty_literal_still_counts_as_ending_in_a_key(self) -> None:
        store = ExpansionStore(
            sections=["Keys"],
            expansions=[Expansion("Keys", "next", "{AHK_KEY:Tab}")],
        )

        output = render_ahk(store)

        self.assertNotIn("A_EndChar", output)

    def test_static_hotstring_omits_ending_character_logic(self) -> None:
        store = ExpansionStore(
            sections=["Common"],
            expansions=[Expansion("Common", "brb", "Be right back")],
        )

        output = render_ahk(store)

        self.assertNotIn("A_EndChar", output)

    def test_case_sensitive_hotstrings_preserve_trigger_case(self) -> None:
        store = ExpansionStore(
            sections=["Case"],
            expansions=[
                Expansion("Case", "Hsa", "Has"),
                Expansion("Case", "hsa", "has"),
                Expansion("Case", "Dyn", 'Today {AHK_EXPR:FormatTime(A_Now, "yyyy-MM-dd")}'),
            ],
        )

        output = render_ahk(store)

        self.assertIn(":CT:Hsa::Has", output)
        self.assertIn(":CT:hsa::has", output)
        self.assertIn(":C:Dyn::\n{", output)

    def test_unsupported_key_raises_clear_error(self) -> None:
        # A name AutoHotkey does not know would be sent as literal text, so the
        # expansion would quietly gain a word instead of pressing something.
        store = ExpansionStore(
            sections=["Keys"],
            expansions=[Expansion("Keys", "badkey", "{AHK_KEY:F5}")],
        )

        with self.assertRaisesRegex(ValueError, "supports only Enter, Tab"):
            render_ahk(store)

    def test_enter_key_generates_a_send_event(self) -> None:
        store = ExpansionStore(
            sections=["Keys"],
            expansions=[Expansion("Keys", "para", "Line one{AHK_KEY:Enter}Line two")],
        )

        output = render_ahk(store)

        self.assertIn('SendEvent("{Enter}")', output)
        self.assertIn('__tem_result .= "Line one"', output)
        self.assertIn('__tem_result .= "Line two"', output)

    def test_an_enter_key_round_trips_through_an_import(self) -> None:
        # The importer reads SendEvent back out of a generated script, so a key
        # it cannot recognise would come home as literal text.
        store = ExpansionStore(
            sections=["Keys"],
            expansions=[Expansion("Keys", ";para", "Line one{AHK_KEY:Enter}Line two")],
        )

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "generated.ahk"
            generate_ahk(store, path, backup=False)
            imported = import_ahk(path)

        self.assertEqual(
            imported.expansions[0].replacement, "Line one{AHK_KEY:Enter}Line two"
        )

    def test_image_placeholder_parses(self) -> None:
        segments = parse_replacement_template(r"{AHK_IMAGE:C:\Users\Scott\Pictures\logo.png}")

        placeholder = segments[0]
        assert isinstance(placeholder, TemplatePlaceholder)
        self.assertEqual(placeholder.kind, "AHK_IMAGE")
        self.assertEqual(placeholder.value, r"C:\Users\Scott\Pictures\logo.png")

    def test_image_placeholder_generates_clipboard_paste_logic(self) -> None:
        store = ExpansionStore(
            sections=["Images"],
            expansions=[Expansion("Images", "logo", r"Logo: {AHK_IMAGE:C:\Images\logo.png}")],
        )

        output = render_ahk(store)

        self.assertIn("TEM_PasteImage(imagePath)", output)
        self.assertIn('if (!TEM_PasteImage("C:\\Images\\logo.png"))', output)
        self.assertIn('MsgBox("Image file not found:', output)
        self.assertIn('Send("^v")', output)

    def test_empty_image_placeholder_raises_clear_error(self) -> None:
        store = ExpansionStore(
            sections=["Images"],
            expansions=[Expansion("Images", "badimage", "{AHK_IMAGE:}")],
        )

        with self.assertRaisesRegex(ValueError, "requires an image file path"):
            render_ahk(store)

    def test_static_spaced_hyphen_uses_paste_delivery(self) -> None:
        # Word autoformats a typed " - " into an en dash; pasting avoids that, so
        # a literal spaced hyphen switches the expansion to TEM_Paste.
        store = ExpansionStore(
            sections=["Work"],
            expansions=[Expansion("Work", "co", "Acme - Widgets")],
        )

        output = render_ahk(store)

        self.assertIn("TEM_Paste(text) {", output)
        self.assertIn(":C:co::\n{", output)
        self.assertIn('TEM_Paste("Acme - Widgets")', output)
        self.assertNotIn(":CT:co::Acme - Widgets", output)

    def test_static_leading_dash_uses_paste_delivery(self) -> None:
        # A leading "- " (e.g. a signature dash) is also autoformatted by Word.
        store = ExpansionStore(
            sections=["Email"],
            expansions=[Expansion("Email", "-sig", "- Crane & Associates")],
        )

        output = render_ahk(store)

        self.assertIn('TEM_Paste("- Crane & Associates")', output)
        self.assertNotIn(":CT:-sig::", output)

    def test_static_double_hyphen_uses_paste_delivery(self) -> None:
        store = ExpansionStore(
            sections=["Work"],
            expansions=[Expansion("Work", "dd", "wait--stop")],
        )

        output = render_ahk(store)

        self.assertIn('TEM_Paste("wait--stop")', output)

    def test_paste_helper_preserves_existing_clipboard(self) -> None:
        store = ExpansionStore(
            sections=["Work"],
            expansions=[Expansion("Work", "co", "Acme - Widgets")],
        )

        output = render_ahk(store)

        # Clipboard is backed up before use and restored afterwards.
        self.assertIn("saved := ClipboardAll()", output)
        self.assertIn("A_Clipboard := text", output)
        self.assertIn("A_Clipboard := saved", output)

    def test_hyphen_without_surrounding_spaces_stays_static(self) -> None:
        # A hyphen touching characters (dates, hyphenated words) is not
        # autoformatted by Word, so it stays a plain static hotstring.
        store = ExpansionStore(
            sections=["Common"],
            expansions=[Expansion("Common", "em", "well-known")],
        )

        output = render_ahk(store)

        self.assertIn(":CT:em::well-known", output)
        self.assertNotIn("TEM_Paste", output)

    def test_dynamic_spaced_hyphen_literal_uses_paste_delivery(self) -> None:
        store = ExpansionStore(
            sections=["Work"],
            expansions=[
                Expansion(
                    "Work",
                    "ach",
                    "{AHK_INPUT:name|Client|Client|} - ACH",
                )
            ],
        )

        output = render_ahk(store)

        self.assertIn("TEM_Paste(__tem_result)", output)
        self.assertNotIn("SendText(__tem_result)", output)
        # The input dialog is unaffected by paste delivery.
        self.assertIn(
            'Map("name", "name", "label", "Client", "title", "Client", '
            '"kind", "input", "default", "")',
            output,
        )

    def test_placeholder_argument_hyphen_does_not_trigger_paste(self) -> None:
        # A " - " inside a prompt/option string is not part of the emitted text,
        # so it must not switch the expansion to paste delivery.
        store = ExpansionStore(
            sections=["Work"],
            expansions=[
                Expansion(
                    "Work",
                    "pick",
                    "{AHK_SELECT:dir|In - Out|Selection|Deposit||Withdrawal}",
                )
            ],
        )

        output = render_ahk(store)

        self.assertIn("SendText(__tem_result)", output)
        self.assertNotIn("TEM_Paste", output)


class AhkStringEscapingTests(unittest.TestCase):
    """AHK opens a comment at a semicolon that follows whitespace, and does so
    inside a quoted string too, truncating the literal and failing to parse."""

    def test_semicolon_prefixed_trigger_is_escaped_in_the_window_title(self) -> None:
        store = ExpansionStore(
            sections=["Work"],
            expansions=[Expansion("Work", ";achs", "Hi {AHK_INPUT:name|Name|Name|}")],
        )

        output = render_ahk(store)

        self.assertIn('TEM_Form("Text Expansion Manager - `;achs"', output)
        self.assertNotIn('Manager - ;achs"', output)

    def test_semicolons_in_prompts_and_options_are_escaped(self) -> None:
        store = ExpansionStore(
            sections=["Status"],
            expansions=[
                Expansion(
                    "Status",
                    ";st",
                    "{AHK_SELECT:state|Pick ; one|State|Open ; now||Closed}",
                )
            ],
        )

        output = render_ahk(store)

        self.assertIn('"Pick `; one"', output)
        self.assertIn('"Open `; now"', output)

    def test_semicolon_in_a_static_replacement_is_escaped(self) -> None:
        # A static expansion is emitted as bare hotstring text, not a quoted
        # string, and an unescaped semicolon there truncates it silently.
        store = ExpansionStore(
            sections=["Work"],
            expansions=[Expansion("Work", ";n", "See ; footnote")],
        )

        output = render_ahk(store)

        self.assertIn(":;n::See `; footnote", output)
        self.assertNotIn(":;n::See ; footnote", output)

    def test_backtick_in_a_static_replacement_is_escaped(self) -> None:
        store = ExpansionStore(
            sections=["Work"],
            expansions=[Expansion("Work", ";b", "a `n b")],
        )

        self.assertIn(":;b::a ``n b", render_ahk(store))


class PromptChromeTests(unittest.TestCase):
    """Branding and theming of the generated prompt windows."""

    def _prompt_store(self) -> ExpansionStore:
        return ExpansionStore(
            sections=["Work"],
            expansions=[
                Expansion("Work", "dear", "Dear {AHK_INPUT:name|Enter name|Name|},")
            ],
        )

    def test_prompt_window_is_titled_with_the_app_and_trigger(self) -> None:
        output = render_ahk(self._prompt_store())

        self.assertIn('TEM_Form("Text Expansion Manager - dear"', output)

    def test_script_looks_in_the_config_folder_then_beside_itself(self) -> None:
        # Both paths hang off A_ScriptDir, so a script generated into another
        # folder, or carried to a machine with no app on it, looks where it
        # actually sits rather than where this app happens to be installed.
        output = render_ahk(self._prompt_store())

        self.assertIn(
            f'inConfig := A_ScriptDir "\\{AHK_CONFIG_DIR_NAME}\\{AHK_ICON_NAME}"', output
        )
        self.assertIn(f'beside := A_ScriptDir "\\{AHK_ICON_NAME}"', output)
        self.assertLess(
            output.index("inConfig :="),
            output.index("beside :="),
            "the config folder has to be searched first",
        )

    def test_the_tray_and_the_prompts_share_one_icon_search(self) -> None:
        output = render_ahk(self._prompt_store())

        self.assertIn("TEM_TrayIcon := TEM_IconPath()", output)
        self.assertIn("    TraySetIcon(TEM_TrayIcon)", output)
        self.assertIn("    iconPath := TEM_IconPath()", output)
        self.assertEqual(output.count("TEM_IconPath() {"), 1)

    def test_a_static_only_script_can_still_find_its_icon(self) -> None:
        # The helper is emitted for every script, not only the ones with
        # prompts: the tray line calls it unconditionally.
        output = render_ahk(
            ExpansionStore(sections=["Work"], expansions=[Expansion("Work", "brb", "back")])
        )

        self.assertIn("TEM_IconPath() {", output)

    def test_light_theme_uses_the_light_palette(self) -> None:
        output = render_ahk(self._prompt_store(), "light")
        light = AHK_THEME_COLORS["light"]

        self.assertIn(f'formGui.BackColor := "{light["bg"]}"', output)
        self.assertIn(f'formGui.SetFont("s9 c{light["text"]}", "Segoe UI")', output)
        self.assertIn("TEM_DarkTitleBar(hwnd, 0)", output)

    def test_dark_theme_uses_the_dark_palette_and_dark_title_bar(self) -> None:
        output = render_ahk(self._prompt_store(), "dark")
        dark = AHK_THEME_COLORS["dark"]

        self.assertIn(f'formGui.BackColor := "{dark["bg"]}"', output)
        self.assertIn(f'formGui.SetFont("s9 c{dark["text"]}", "Segoe UI")', output)
        self.assertIn(f"Background{dark['field']}", output)
        self.assertIn("TEM_DarkTitleBar(hwnd, 1)", output)

    def _select_store(self) -> ExpansionStore:
        return ExpansionStore(
            sections=["Work"],
            expansions=[
                Expansion("Work", "pick", "{AHK_SELECT:kind|Kind|Kind|Invoice||Receipt}")
            ],
        )

    def test_dark_theme_restyles_the_system_drawn_controls(self) -> None:
        # Buttons, dropdowns and scrollbars are drawn by Windows and ignore the
        # Gui's colours, so they need the dark visual styles applied by hand.
        output = render_ahk(self._prompt_store(), "dark")

        self.assertIn('DllCall("uxtheme\\SetWindowTheme"', output)
        self.assertIn('TEM_ThemeControl(okButton.Hwnd, "DarkMode_Explorer")', output)
        self.assertIn('TEM_ThemeControl(cancelButton.Hwnd, "DarkMode_Explorer")', output)
        self.assertIn('TEM_ThemeControl(preview.Hwnd, "DarkMode_Explorer")', output)

    def test_light_theme_leaves_the_default_visual_styles(self) -> None:
        # Applying a DarkMode style in the light theme would invert the
        # controls. The call sites still name one -- they are identical in both
        # themes -- but the helper they call is inert here.
        output = render_ahk(self._prompt_store(), "light")

        self.assertIn("TEM_ThemeControl(", output)
        self.assertNotIn("SetWindowTheme", output)

    def test_a_dropdown_uses_the_combo_box_dark_style(self) -> None:
        # A combo box needs DarkMode_CFD; DarkMode_Explorer leaves it light.
        output = render_ahk(self._select_store(), "dark")

        self.assertIn('TEM_ThemeControl(dropdown.Hwnd, "DarkMode_CFD")', output)

    def test_form_fields_use_the_combo_box_dark_style(self) -> None:
        # Both field kinds take DarkMode_CFD: the dropdown for its face, the
        # edit because DarkMode_Explorer leaves an edit's frame light.
        store = ExpansionStore(
            sections=["Work"],
            expansions=[
                Expansion(
                    "Work",
                    "both",
                    "{AHK_INPUT:name|Name|Name|} {AHK_SELECT:kind|Kind|Kind|A||B}",
                )
            ],
        )

        output = render_ahk(store, "dark")

        self.assertEqual(output.count('TEM_ThemeControl(ctrl.Hwnd, "DarkMode_CFD")'), 2)

    def test_dark_theme_drops_the_light_frame_around_fields(self) -> None:
        # WS_EX_CLIENTEDGE is drawn light whatever style the control carries,
        # so a dark field was ringed in white until it was removed.
        output = render_ahk(self._prompt_store(), "dark")

        self.assertIn("Multi ReadOnly -E0x200 Background", output)
        self.assertIn('AddEdit("x+8 yp-4 w312 -E0x200 Background', output)

    def test_light_theme_keeps_the_frame_around_fields(self) -> None:
        output = render_ahk(self._prompt_store(), "light")

        self.assertNotIn("-E0x200", output)

    def test_unknown_theme_falls_back_to_light(self) -> None:
        self.assertEqual(
            render_ahk(self._prompt_store(), "solarized"),
            render_ahk(self._prompt_store(), "light"),
        )

    def test_generate_copies_the_icon_into_the_scripts_config_folder(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "app.ico"
            source.write_bytes(b"icon-bytes")
            ahk_path = Path(temp_dir) / "out" / "gen.ahk"

            generate_ahk(
                self._prompt_store(), ahk_path, backup=False, icon_source=source
            )

            copied = ahk_path.parent / AHK_CONFIG_DIR_NAME / AHK_ICON_NAME
            self.assertTrue(copied.is_file(), "the config folder was not created")
            self.assertEqual(copied.read_bytes(), b"icon-bytes")

    def test_generate_clears_away_the_copy_older_builds_left_loose(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "app.ico"
            source.write_bytes(b"icon-bytes")
            ahk_path = Path(temp_dir) / "gen.ahk"
            stale = ahk_path.parent / AHK_ICON_NAME
            stale.write_bytes(b"icon-bytes")

            generate_ahk(
                self._prompt_store(), ahk_path, backup=False, icon_source=source
            )

            self.assertFalse(stale.exists(), "the superseded copy was left behind")
            self.assertTrue(
                (ahk_path.parent / AHK_CONFIG_DIR_NAME / AHK_ICON_NAME).is_file()
            )

    def test_generate_leaves_an_icon_it_did_not_put_there(self) -> None:
        # Only a byte-for-byte copy of what this app installs is ours to
        # remove. Anything else the user put there, and the script still falls
        # back to it.
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "app.ico"
            source.write_bytes(b"icon-bytes")
            ahk_path = Path(temp_dir) / "gen.ahk"
            theirs = ahk_path.parent / AHK_ICON_NAME
            theirs.write_bytes(b"a different icon")

            generate_ahk(
                self._prompt_store(), ahk_path, backup=False, icon_source=source
            )

            self.assertEqual(theirs.read_bytes(), b"a different icon")

    def test_generate_survives_a_missing_icon_source(self) -> None:
        with TemporaryDirectory() as temp_dir:
            ahk_path = Path(temp_dir) / "gen.ahk"

            generate_ahk(
                self._prompt_store(),
                ahk_path,
                backup=False,
                icon_source=Path(temp_dir) / "absent.ico",
            )

            self.assertTrue(ahk_path.is_file())
            self.assertFalse(
                (ahk_path.parent / AHK_CONFIG_DIR_NAME / AHK_ICON_NAME).exists()
            )


if __name__ == "__main__":
    unittest.main()
