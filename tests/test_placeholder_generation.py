import unittest

from ahk_manager import (
    Expansion,
    ExpansionStore,
    TemplatePlaceholder,
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
        self.assertIn('__tem_vals := TEM_Form("dear", __tem_fields, __tem_parts)', output)
        self.assertIn('client_name := __tem_vals["client_name"]', output)
        self.assertIn("__tem_result .= client_name", output)
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
        self.assertEqual(output.count('amount := __tem_vals["amount"]'), 1)
        self.assertEqual(output.count("__tem_result .= amount"), 2)

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

        self.assertIn("TEM_Select(prompt, title, options)", output)
        self.assertIn(
            '__tem_select_status := TEM_Select("Choose status", "Status", ["Pending", "Approved", "Rejected"])',
            output,
        )
        self.assertIn("status := __tem_select_status.value", output)

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
        store = ExpansionStore(
            sections=["Keys"],
            expansions=[Expansion("Keys", "badkey", "{AHK_KEY:Enter}")],
        )

        with self.assertRaisesRegex(ValueError, "supports only Tab"):
            render_ahk(store)

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


if __name__ == "__main__":
    unittest.main()
