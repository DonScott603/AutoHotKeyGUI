import unittest

from ahk_manager import Expansion, ExpansionStore, parse_replacement_template, render_ahk


class PlaceholderGenerationTests(unittest.TestCase):
    def test_literal_expansion_stays_one_line(self) -> None:
        store = ExpansionStore(
            sections=["Common"],
            expansions=[Expansion("Common", "brb", "Be right back")],
        )

        output = render_ahk(store)

        self.assertIn("::brb::Be right back", output)
        self.assertNotIn("SendText(__tem_result)", output)

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

        self.assertIn("::today::\n{", output)
        self.assertIn('__tem_result .= "Today is "', output)
        self.assertIn('__tem_result .= FormatTime(A_Now, "yyyy-MM-dd")', output)
        self.assertIn("SendText(__tem_result)", output)

    def test_input_placeholder_generates_inputbox_logic(self) -> None:
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

        self.assertIn('InputBox("Enter client name", "Client Name", , "")', output)
        self.assertIn("client_name := __tem_input_client_name.Value", output)
        self.assertIn("__tem_result .= client_name", output)

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

        self.assertEqual(segments[1].kind, "AHK_KEY")
        self.assertEqual(segments[1].value, "Tab")

    def test_tab_placeholder_generates_dynamic_hotstring(self) -> None:
        store = ExpansionStore(
            sections=["Keys"],
            expansions=[Expansion("Keys", "tabbed", "Left{AHK_KEY:Tab}Right")],
        )

        output = render_ahk(store)

        self.assertIn("::tabbed::\n{", output)
        self.assertIn('Send("{Tab}")', output)
        self.assertIn('__tem_result .= "Left"', output)
        self.assertIn('__tem_result .= "Right"', output)
        self.assertNotIn("::tabbed::Left", output)

    def test_unsupported_key_raises_clear_error(self) -> None:
        store = ExpansionStore(
            sections=["Keys"],
            expansions=[Expansion("Keys", "badkey", "{AHK_KEY:Enter}")],
        )

        with self.assertRaisesRegex(ValueError, "supports only Tab"):
            render_ahk(store)

    def test_image_placeholder_parses(self) -> None:
        segments = parse_replacement_template(r"{AHK_IMAGE:C:\Users\Scott\Pictures\logo.png}")

        self.assertEqual(segments[0].kind, "AHK_IMAGE")
        self.assertEqual(segments[0].value, r"C:\Users\Scott\Pictures\logo.png")

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


if __name__ == "__main__":
    unittest.main()
