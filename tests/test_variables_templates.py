import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ahk_manager import (
    Expansion,
    ExpansionStore,
    TemplateDef,
    VariableDef,
    render_ahk,
    validate_templates,
    validate_variables,
)
from app import ExpansionApp


class VariableTemplateTests(unittest.TestCase):
    def test_variable_and_template_storage_round_trip(self) -> None:
        store = ExpansionStore(
            sections=["General"],
            expansions=[],
            variables=[
                VariableDef("client_name", "text_input", "Enter client name", "", [], "Client"),
                VariableDef("status", "list_selection", "Choose status", "", ["Pending"], ""),
            ],
            templates=[
                TemplateDef("Client Follow-Up", "Follow-up note", "Dear {VAR:client_name},", ""),
            ],
        )

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "expansions.json"
            store.save(path)
            loaded = ExpansionStore.load(path)

        self.assertEqual(loaded.variables[0].name, "client_name")
        self.assertEqual(loaded.variables[1].list_options, ["Pending"])
        self.assertEqual(loaded.templates[0].body, "Dear {VAR:client_name},")

    def test_text_input_variable_resolves_to_input_logic(self) -> None:
        store = ExpansionStore(
            sections=["Letters"],
            expansions=[Expansion("Letters", "dear", "Dear {VAR:client_name},")],
            variables=[VariableDef("client_name", "text_input", "Enter client name", "", [], "")],
        )

        output = render_ahk(store)

        self.assertIn('InputBox("Enter client name", "Client Name", , "")', output)
        self.assertIn("client_name := __tem_input_client_name.Value", output)

    def test_list_selection_variable_resolves_to_select_logic(self) -> None:
        store = ExpansionStore(
            sections=["Status"],
            expansions=[Expansion("Status", "st", "Status: {VAR:status}")],
            variables=[
                VariableDef("status", "list_selection", "Choose status", "", ["Pending", "Approved"], ""),
            ],
        )

        output = render_ahk(store)

        self.assertIn("TEM_Select(prompt, title, options)", output)
        self.assertIn('__tem_select_status := TEM_Select("Choose status", "Status", ["Pending", "Approved"])', output)

    def test_date_time_variable_resolves_to_format_time_expression(self) -> None:
        store = ExpansionStore(
            sections=["Dates"],
            expansions=[Expansion("Dates", "today", "Today is {VAR:today_iso}")],
            variables=[VariableDef("today_iso", "date_time", "", "yyyy-MM-dd", [], "")],
        )

        output = render_ahk(store)

        self.assertIn('__tem_result .= FormatTime(A_Now, "yyyy-MM-dd")', output)

    def test_undefined_variable_raises_clear_error(self) -> None:
        store = ExpansionStore(
            sections=["Bad"],
            expansions=[Expansion("Bad", "bad", "{VAR:missing}")],
        )

        with self.assertRaisesRegex(ValueError, 'Undefined variable "missing"'):
            render_ahk(store)

    def test_template_insertion_inserts_body_text(self) -> None:
        app = ExpansionApp()
        try:
            app.store.templates = [
                TemplateDef("Client Follow-Up", "", "Dear {VAR:client_name},", ""),
            ]
            app.replacement_text.delete("1.0", "end")
            app.insert_snippet("{TPL:Client Follow-Up}", app.replacement_text)
            self.assertEqual(app.replacement_text.get("1.0", "end-1c"), "{TPL:Client Follow-Up}")
        finally:
            app.destroy()

    def test_variable_placeholder_syntax(self) -> None:
        app = ExpansionApp()
        try:
            app.replacement_text.delete("1.0", "end")
            app.insert_replacement_snippet("{VAR:client_name}")
            self.assertEqual(app.replacement_text.get("1.0", "end-1c"), "{VAR:client_name}")
        finally:
            app.destroy()

    def test_template_body_accepts_variable_insertion_syntax(self) -> None:
        app = ExpansionApp()
        try:
            app.template_body_text.delete("1.0", "end")
            app.insert_snippet("{VAR:client_name}", app.template_body_text)
            self.assertEqual(app.template_body_text.get("1.0", "end-1c"), "{VAR:client_name}")
        finally:
            app.destroy()

    def test_template_body_accepts_date_time_placeholder(self) -> None:
        app = ExpansionApp()
        try:
            snippet = '{AHK_EXPR:FormatTime(A_Now, "yyyy-MM-dd")}'
            app.template_body_text.delete("1.0", "end")
            app.insert_snippet(snippet, app.template_body_text)
            self.assertEqual(app.template_body_text.get("1.0", "end-1c"), snippet)
        finally:
            app.destroy()

    def test_template_body_accepts_input_placeholder(self) -> None:
        app = ExpansionApp()
        try:
            snippet = "{AHK_INPUT:client_name|Enter client name|Client Name|}"
            app.template_body_text.delete("1.0", "end")
            app.insert_snippet(snippet, app.template_body_text)
            self.assertEqual(app.template_body_text.get("1.0", "end-1c"), snippet)
        finally:
            app.destroy()

    def test_template_body_accepts_list_selection_placeholder(self) -> None:
        app = ExpansionApp()
        try:
            snippet = "{AHK_SELECT:status|Choose status|Status|Pending||Approved}"
            app.template_body_text.delete("1.0", "end")
            app.insert_snippet(snippet, app.template_body_text)
            self.assertEqual(app.template_body_text.get("1.0", "end-1c"), snippet)
        finally:
            app.destroy()

    def test_template_body_accepts_image_placeholder(self) -> None:
        app = ExpansionApp()
        try:
            snippet = r"{AHK_IMAGE:C:\Images\logo.png}"
            app.template_body_text.delete("1.0", "end")
            app.insert_snippet(snippet, app.template_body_text)
            self.assertEqual(app.template_body_text.get("1.0", "end-1c"), snippet)
        finally:
            app.destroy()

    def test_template_body_accepts_tab_placeholder(self) -> None:
        app = ExpansionApp()
        try:
            app.template_body_text.delete("1.0", "end")
            app.insert_snippet("{AHK_KEY:Tab}", app.template_body_text)
            self.assertEqual(app.template_body_text.get("1.0", "end-1c"), "{AHK_KEY:Tab}")
        finally:
            app.destroy()

    def test_template_nesting_resolves_when_used_by_expansion(self) -> None:
        store = ExpansionStore(
            sections=["Letters"],
            expansions=[Expansion("Letters", "follow", "{TPL:Client Follow-Up}")],
            variables=[VariableDef("client_name", "text_input", "Enter client name", "", [], "")],
            templates=[
                TemplateDef("Greeting", body="Dear {VAR:client_name},"),
                TemplateDef("Client Follow-Up", body="{TPL:Greeting}\nThank you."),
            ],
        )

        output = render_ahk(store)

        self.assertIn('InputBox("Enter client name", "Client Name", , "")', output)
        self.assertIn('__tem_result .= "`nThank you."', output)

    def test_circular_template_reference_is_rejected(self) -> None:
        store = ExpansionStore(
            sections=["Letters"],
            expansions=[Expansion("Letters", "bad", "{TPL:A}")],
            templates=[
                TemplateDef("A", body="{TPL:B}"),
                TemplateDef("B", body="{TPL:A}"),
            ],
        )

        with self.assertRaisesRegex(ValueError, "Circular template reference detected: A -> B -> A"):
            render_ahk(store)

    def test_self_reference_is_rejected(self) -> None:
        store = ExpansionStore(
            sections=["Letters"],
            expansions=[Expansion("Letters", "bad", "{TPL:A}")],
            templates=[TemplateDef("A", body="{TPL:A}")],
        )

        with self.assertRaisesRegex(ValueError, "Circular template reference detected: A -> A"):
            render_ahk(store)

    def test_template_editor_has_same_insertion_buttons(self) -> None:
        app = ExpansionApp()
        try:
            labels = set()

            def walk(widget):
                for child in widget.winfo_children():
                    if child.winfo_class() == "TButton":
                        labels.add(child.cget("text"))
                    walk(child)

            walk(app.template_body_text.master)
            for label in {
                "Insert Variable",
                "Insert Template",
                "Insert Date/Time",
                "Insert Input Box",
                "Insert List Selection",
                "Insert Tab",
                "Insert Image",
            }:
                self.assertIn(label, labels)
        finally:
            app.destroy()

    def test_duplicate_variable_names_are_invalid(self) -> None:
        with self.assertRaisesRegex(ValueError, "Duplicate variable name"):
            validate_variables([
                VariableDef("client_name", "text_input"),
                VariableDef("client_name", "text_input"),
            ])

    def test_duplicate_template_names_are_invalid(self) -> None:
        with self.assertRaisesRegex(ValueError, "Duplicate template name"):
            validate_templates([
                TemplateDef("Follow Up", body="One"),
                TemplateDef("Follow Up", body="Two"),
            ])

    def test_literal_only_expansion_still_generates_simple_hotstring(self) -> None:
        store = ExpansionStore(
            sections=["Common"],
            expansions=[Expansion("Common", "brb", "Be right back")],
        )

        self.assertIn(":C:brb::Be right back", render_ahk(store))


if __name__ == "__main__":
    unittest.main()
