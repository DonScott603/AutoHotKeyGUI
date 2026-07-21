import unittest

from ahk_manager import (
    Expansion,
    ExpansionStore,
    TemplateDef,
    VariableDef,
    collect_placeholder_summary,
    parse_replacement_template,
    resolve_expansion_preview,
    resolve_template_preview,
    resolve_variable_preview,
)


class PreviewTests(unittest.TestCase):
    def test_expansion_preview_literal_only(self) -> None:
        store = ExpansionStore(["Common"], [Expansion("Common", "brb", "Be right back")])

        preview = resolve_expansion_preview(store.expansions[0], store)

        self.assertIn("Raw Replacement Text", preview.content)
        self.assertIn("Resolved Replacement Text", preview.content)
        self.assertIn("No placeholders found.", preview.content)

    def test_expansion_preview_with_variables(self) -> None:
        store = ExpansionStore(
            ["Letters"],
            [Expansion("Letters", "dear", "Dear {VAR:client_name},")],
            [VariableDef("client_name", "text_input", "Enter client name")],
        )

        preview = resolve_expansion_preview(store.expansions[0], store)

        self.assertIn("{AHK_INPUT:client_name|Enter client name|Client Name|}", preview.content)
        self.assertIn("Variables: client_name", preview.content)

    def test_expansion_preview_with_template_reference(self) -> None:
        store = ExpansionStore(
            ["Letters"],
            [Expansion("Letters", "follow", "{TPL:Follow Up}")],
            templates=[TemplateDef("Follow Up", body="Thanks")],
        )

        preview = resolve_expansion_preview(store.expansions[0], store)

        self.assertIn("Thanks", preview.content)
        self.assertIn("Nested templates: Follow Up", preview.content)

    def test_expansion_preview_with_date_time_placeholder_and_generated_code(self) -> None:
        store = ExpansionStore(
            ["Dates"],
            [Expansion("Dates", "today", 'Today {AHK_EXPR:FormatTime(A_Now, "yyyy-MM-dd")}')],
        )

        preview = resolve_expansion_preview(store.expansions[0], store)

        self.assertIn("Date/Time: 1", preview.content)
        self.assertIn("Generated AutoHotkey v2 Code", preview.content)
        self.assertIn('__tem_result .= FormatTime(A_Now, "yyyy-MM-dd")', preview.content)

    def test_variable_preview_text_input(self) -> None:
        preview = resolve_variable_preview(VariableDef("client_name", "text_input", "Enter client name"))

        self.assertIn("{VAR:client_name}", preview.content)
        self.assertIn("{AHK_INPUT:client_name|Enter client name|Client Name|}", preview.content)

    def test_variable_preview_list_selection(self) -> None:
        preview = resolve_variable_preview(
            VariableDef("status", "list_selection", "Choose status", "", ["Pending", "Approved"])
        )

        self.assertIn("{AHK_SELECT:status|Choose status|Status|Pending||Approved}", preview.content)

    def test_variable_preview_date_time(self) -> None:
        preview = resolve_variable_preview(VariableDef("today_iso", "date_time", "", "yyyy-MM-dd"))

        self.assertIn('{AHK_EXPR:FormatTime(A_Now, "yyyy-MM-dd")}', preview.content)

    def test_template_preview_with_variables(self) -> None:
        store = ExpansionStore(
            variables=[VariableDef("client_name", "text_input", "Enter client name")],
            templates=[TemplateDef("Greeting", body="Dear {VAR:client_name},")],
        )

        preview = resolve_template_preview(store.templates[0], store)

        self.assertIn("{AHK_INPUT:client_name|Enter client name|Client Name|}", preview.content)

    def test_template_preview_with_nested_templates(self) -> None:
        store = ExpansionStore(
            templates=[
                TemplateDef("Greeting", body="Hello"),
                TemplateDef("Follow Up", body="{TPL:Greeting}\nThanks"),
            ],
        )

        preview = resolve_template_preview(store.templates[1], store)

        self.assertIn("Hello", preview.content)
        self.assertIn("Nested templates: Greeting", preview.content)

    def test_template_preview_detects_undefined_variable(self) -> None:
        store = ExpansionStore(templates=[TemplateDef("Bad", body="{VAR:missing}")])

        with self.assertRaisesRegex(ValueError, 'Undefined variable "missing"'):
            resolve_template_preview(store.templates[0], store)

    def test_template_preview_detects_undefined_template(self) -> None:
        store = ExpansionStore(templates=[TemplateDef("Bad", body="{TPL:missing}")])

        with self.assertRaisesRegex(ValueError, 'Undefined template "missing"'):
            resolve_template_preview(store.templates[0], store)

    def test_template_preview_detects_circular_template_reference(self) -> None:
        store = ExpansionStore(
            templates=[
                TemplateDef("A", body="{TPL:B}"),
                TemplateDef("B", body="{TPL:A}"),
            ],
        )

        with self.assertRaisesRegex(ValueError, "Circular template reference detected: A -> B -> A"):
            resolve_template_preview(store.templates[0], store)

    def test_placeholder_summary_generation(self) -> None:
        store = ExpansionStore(
            variables=[VariableDef("client_name", "text_input", "Enter client name")],
            templates=[TemplateDef("Greeting", body="Hi")],
        )
        segments = parse_replacement_template(
            r"{VAR:client_name} {AHK_KEY:Tab} {AHK_IMAGE:C:\logo.png} {TPL:Greeting}"
        )

        summary = collect_placeholder_summary(segments, store)

        self.assertIn("Variables: client_name", summary)
        self.assertIn("Input boxes: 1", summary)
        self.assertIn("Keystrokes: Tab", summary)
        self.assertIn(r"Images: C:\logo.png", summary)
        self.assertIn("Nested templates: Greeting", summary)


if __name__ == "__main__":
    unittest.main()
