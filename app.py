import shutil
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from ahk_manager import (
    DEFAULT_AHK,
    DEFAULT_JSON,
    DEFAULT_SETTINGS,
    AppSettings,
    Expansion,
    ExpansionStore,
    count_import_conflicts,
    generate_ahk,
    import_ahk,
    merge_imported_store,
    parse_replacement_template,
)


APP_DIR = Path(__file__).resolve().parent
JSON_PATH = APP_DIR / DEFAULT_JSON
AHK_PATH = APP_DIR / DEFAULT_AHK
SETTINGS_PATH = APP_DIR / DEFAULT_SETTINGS
IMAGE_FILE_TYPES = [
    ("Image files", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
    ("PNG files", "*.png"),
    ("JPEG files", "*.jpg *.jpeg"),
    ("GIF files", "*.gif"),
    ("Bitmap files", "*.bmp"),
    ("WebP files", "*.webp"),
    ("All files", "*.*"),
]
TABLE_ACTION_BUTTON_WIDTHS = {
    "New": 8,
    "Edit": 8,
    "Delete": 10,
    "Toggle Enabled": 15,
}
TEMPLATE_ACTION_BUTTON_WIDTHS = {
    "Insert Date/Time": 17,
    "Insert Input Box": 17,
    "Insert List Selection": 19,
    "Insert Tab": 17,
    "Insert Image": 17,
}


def has_reserved_placeholder_chars(value: str) -> bool:
    return any(char in value for char in "{}|")


class ImportConflictDialog(simpledialog.Dialog):
    def __init__(self, parent: tk.Tk, conflict_count: int) -> None:
        self.conflict_count = conflict_count
        self.choice = tk.StringVar(value="skip")
        self.result: str | None = None
        super().__init__(parent, "Import conflicts")

    def body(self, master: tk.Frame) -> tk.Widget:
        ttk.Label(
            master,
            text=(
                f"{self.conflict_count} imported trigger(s) already exist in the same section. "
                "Choose how to handle all conflicts."
            ),
            wraplength=420,
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))
        ttk.Radiobutton(master, text="Skip duplicate triggers", variable=self.choice, value="skip").grid(
            row=1, column=0, sticky="w"
        )
        ttk.Radiobutton(master, text="Overwrite existing expansions", variable=self.choice, value="overwrite").grid(
            row=2, column=0, sticky="w"
        )
        ttk.Radiobutton(master, text="Keep both with renamed trigger", variable=self.choice, value="rename").grid(
            row=3, column=0, sticky="w"
        )
        return master

    def apply(self) -> None:
        self.result = self.choice.get()


class DateTimeDialog(simpledialog.Dialog):
    FORMAT_OPTIONS = {
        "Short date": "MM/dd/yyyy",
        "ISO date": "yyyy-MM-dd",
        "Long date": "dddd, MMMM d, yyyy",
        "Time": "h:mm tt",
        "Date + time": "yyyy-MM-dd h:mm tt",
        "Custom format": "",
    }

    def __init__(self, parent: tk.Tk) -> None:
        self.choice = tk.StringVar(value="ISO date")
        self.custom_format = tk.StringVar()
        self.result: str | None = None
        super().__init__(parent, "Insert Date/Time")

    def body(self, master: tk.Frame) -> tk.Widget:
        master.columnconfigure(1, weight=1)
        ttk.Label(master, text="Format").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
        combo = ttk.Combobox(
            master,
            textvariable=self.choice,
            state="readonly",
            values=list(self.FORMAT_OPTIONS.keys()),
            width=28,
        )
        combo.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        ttk.Label(master, text="Custom").grid(row=1, column=0, sticky="w", padx=(0, 8))
        entry = ttk.Entry(master, textvariable=self.custom_format)
        entry.grid(row=1, column=1, sticky="ew")
        return combo

    def validate(self) -> bool:
        selected = self.choice.get()
        date_format = self.custom_format.get().strip() if selected == "Custom format" else self.FORMAT_OPTIONS[selected]
        if not date_format:
            messagebox.showerror("Date/Time format", "Enter a custom date/time format.", parent=self)
            return False
        if any(char in date_format for char in '{}"'):
            messagebox.showerror("Date/Time format", 'Format cannot contain braces or double quotes.', parent=self)
            return False
        return True

    def apply(self) -> None:
        selected = self.choice.get()
        date_format = self.custom_format.get().strip() if selected == "Custom format" else self.FORMAT_OPTIONS[selected]
        self.result = f'{{AHK_EXPR:FormatTime(A_Now, "{date_format}")}}'


class InputPlaceholderDialog(simpledialog.Dialog):
    def __init__(self, parent: tk.Tk) -> None:
        self.variable = tk.StringVar(value="name")
        self.prompt = tk.StringVar(value="Enter value")
        self.title_text = tk.StringVar(value="Input")
        self.default = tk.StringVar()
        self.result: str | None = None
        super().__init__(parent, "Insert Input Box")

    def body(self, master: tk.Frame) -> tk.Widget:
        master.columnconfigure(1, weight=1)
        first: tk.Widget | None = None
        fields = [
            ("Variable name", self.variable),
            ("Prompt text", self.prompt),
            ("Window title", self.title_text),
            ("Default value", self.default),
        ]
        for row, (label, variable) in enumerate(fields):
            ttk.Label(master, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
            entry = ttk.Entry(master, textvariable=variable, width=34)
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            if first is None:
                first = entry
        return first

    def validate(self) -> bool:
        fields = [self.prompt.get(), self.title_text.get(), self.default.get()]
        if any(has_reserved_placeholder_chars(value) for value in fields):
            messagebox.showerror(
                "Input Box placeholder",
                "Prompt, title, and default value cannot contain braces or pipe characters.",
                parent=self,
            )
            return False
        try:
            parse_replacement_template(self._placeholder())
        except ValueError as exc:
            messagebox.showerror("Input Box placeholder", str(exc), parent=self)
            return False
        return True

    def apply(self) -> None:
        self.result = self._placeholder()

    def _placeholder(self) -> str:
        return (
            "{AHK_INPUT:"
            f"{self.variable.get().strip()}|"
            f"{self.prompt.get().strip()}|"
            f"{self.title_text.get().strip()}|"
            f"{self.default.get().strip()}"
            "}"
        )


class SelectPlaceholderDialog(simpledialog.Dialog):
    def __init__(self, parent: tk.Tk) -> None:
        self.variable = tk.StringVar(value="choice")
        self.prompt = tk.StringVar(value="Choose an option")
        self.title_text = tk.StringVar(value="Selection")
        self.result: str | None = None
        self.options_text: tk.Text | None = None
        super().__init__(parent, "Insert List Selection")

    def body(self, master: tk.Frame) -> tk.Widget:
        master.columnconfigure(1, weight=1)
        fields = [
            ("Variable name", self.variable),
            ("Prompt text", self.prompt),
            ("Window title", self.title_text),
        ]
        first: tk.Widget | None = None
        for row, (label, variable) in enumerate(fields):
            ttk.Label(master, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
            entry = ttk.Entry(master, textvariable=variable, width=34)
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            if first is None:
                first = entry

        ttk.Label(master, text="Options").grid(row=3, column=0, sticky="nw", padx=(0, 8), pady=4)
        self.options_text = tk.Text(master, width=34, height=6, wrap=tk.WORD)
        self.options_text.grid(row=3, column=1, sticky="nsew", pady=4)
        self.options_text.insert("1.0", "Option A\nOption B\nOption C")
        return first

    def validate(self) -> bool:
        fields = [self.prompt.get(), self.title_text.get()]
        if any(has_reserved_placeholder_chars(value) for value in fields):
            messagebox.showerror(
                "List Selection placeholder",
                "Prompt and title cannot contain braces or pipe characters.",
                parent=self,
            )
            return False
        if self.options_text is not None:
            options_text = self.options_text.get("1.0", "end-1c")
            if any(char in options_text for char in "{}|"):
                messagebox.showerror(
                    "List Selection placeholder",
                    "Options cannot contain braces or pipe characters.",
                    parent=self,
                )
                return False
        try:
            parse_replacement_template(self._placeholder())
        except ValueError as exc:
            messagebox.showerror("List Selection placeholder", str(exc), parent=self)
            return False
        return True

    def apply(self) -> None:
        self.result = self._placeholder()

    def _placeholder(self) -> str:
        options = []
        if self.options_text is not None:
            options = [
                line.strip()
                for line in self.options_text.get("1.0", "end-1c").splitlines()
                if line.strip()
            ]
        return (
            "{AHK_SELECT:"
            f"{self.variable.get().strip()}|"
            f"{self.prompt.get().strip()}|"
            f"{self.title_text.get().strip()}|"
            f"{'||'.join(options)}"
            "}"
        )


class ExpansionApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("AutoHotkey Text Expansion Manager")
        self.geometry("1120x680")
        self.minsize(900, 560)

        self.store = self._load_store()
        self.settings = self._load_settings()
        self.ahk_process: subprocess.Popen | None = None
        self.selected_section = tk.StringVar(value=self.store.sections[0])
        self.search_var = tk.StringVar()
        self.current_expansion: Expansion | None = None

        self.section_var = tk.StringVar()
        self.trigger_var = tk.StringVar()
        self.enabled_var = tk.BooleanVar(value=True)
        self.ahk_path_var = tk.StringVar(value=self.settings.generated_ahk_path)
        self.status_var = tk.StringVar(value="Ready.")

        self._build_ui()
        self.refresh_sections()
        self.refresh_expansions()

    def _load_store(self) -> ExpansionStore:
        try:
            return ExpansionStore.load(JSON_PATH)
        except ValueError as exc:
            messagebox.showerror("Load error", str(exc))
            return ExpansionStore()

    def _load_settings(self) -> AppSettings:
        try:
            return AppSettings.load(SETTINGS_PATH, AHK_PATH)
        except ValueError as exc:
            messagebox.showerror("Settings error", str(exc))
            return AppSettings(str(AHK_PATH))

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.grid(row=0, column=0, sticky="nsew")

        left = ttk.Frame(paned, padding=8)
        center = ttk.Frame(paned, padding=(8, 8, 4, 8))
        right = ttk.Frame(paned, padding=(4, 8, 8, 8))
        paned.add(left, weight=0)
        paned.add(center, weight=5)
        paned.add(right, weight=2)
        paned.pane(left, weight=0)
        paned.pane(center, weight=5)
        paned.pane(right, weight=2)
        try:
            paned.pane(left, minsize=190)
            paned.pane(center, minsize=420)
            paned.pane(right, minsize=280)
        except tk.TclError:
            pass

        self._build_sections(left)
        self._build_table(center)
        self._build_form(right)

        self._build_output_settings(self)

        footer = ttk.Frame(self, padding=(8, 0, 8, 8))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)

        ttk.Label(footer, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="Save JSON", command=self.save_json).grid(row=0, column=1, padx=4)
        ttk.Button(footer, text="Generate .ahk", command=self.generate_ahk).grid(row=0, column=2, padx=4)
        ttk.Button(footer, text="Run AHK", command=self.run_ahk).grid(row=0, column=3, padx=4)
        ttk.Button(footer, text="Reload AHK", command=self.reload_ahk).grid(row=0, column=4, padx=4)
        ttk.Button(footer, text="Import .ahk", command=self.import_ahk).grid(row=0, column=5, padx=4)

    def _build_output_settings(self, parent: tk.Widget) -> None:
        output_frame = ttk.Frame(parent, padding=(8, 4, 8, 8))
        output_frame.grid(row=1, column=0, sticky="ew")
        output_frame.columnconfigure(1, weight=1)

        ttk.Label(output_frame, text="Generated AHK path").grid(row=0, column=0, sticky="w", padx=(0, 8))
        path_entry = ttk.Entry(output_frame, textvariable=self.ahk_path_var)
        path_entry.grid(row=0, column=1, sticky="ew")
        path_entry.bind("<FocusOut>", lambda _event: self.save_settings())
        path_entry.bind("<Return>", lambda _event: self.save_settings())
        ttk.Button(output_frame, text="Browse", width=10, command=self.browse_ahk_path).grid(row=0, column=2, padx=(8, 0))

    def _build_sections(self, parent: ttk.Frame) -> None:
        parent.configure(width=210)
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1, minsize=190)
        parent.grid_propagate(False)

        ttk.Label(parent, text="Sections").grid(row=0, column=0, sticky="ew")
        self.section_list = tk.Listbox(parent, exportselection=False)
        self.section_list.grid(row=1, column=0, sticky="nsew", pady=(6, 8))
        self.section_list.bind("<<ListboxSelect>>", self.on_section_select)

        button_frame = ttk.Frame(parent)
        button_frame.grid(row=2, column=0, sticky="ew")
        button_frame.columnconfigure((0, 1, 2), weight=0)
        ttk.Button(button_frame, text="Add", width=8, command=self.add_section).grid(row=0, column=0, padx=(0, 4))
        ttk.Button(button_frame, text="Rename", width=9, command=self.rename_section).grid(row=0, column=1, padx=4)
        ttk.Button(button_frame, text="Delete", width=8, command=self.delete_section).grid(row=0, column=2, padx=(4, 0))

    def _build_table(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(2, weight=1)
        parent.columnconfigure(0, weight=1)

        search_frame = ttk.Frame(parent)
        search_frame.grid(row=0, column=0, sticky="ew")
        search_frame.columnconfigure(1, weight=1)
        ttk.Label(search_frame, text="Search").grid(row=0, column=0, sticky="w", padx=(0, 8))
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        search_entry.grid(row=0, column=1, sticky="ew")
        search_entry.bind("<KeyRelease>", lambda _event: self.refresh_expansions())
        ttk.Button(search_frame, text="Clear", command=self.clear_search).grid(row=0, column=2, padx=(8, 0))

        self.duplicate_label = ttk.Label(parent, foreground="#9a3412")
        self.duplicate_label.grid(row=1, column=0, sticky="w", pady=(8, 4))

        columns = ("enabled", "trigger", "replacement", "notes")
        self.tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("enabled", text="On")
        self.tree.heading("trigger", text="Trigger")
        self.tree.heading("replacement", text="Replacement")
        self.tree.heading("notes", text="Notes")
        self.tree.column("enabled", width=48, stretch=False, anchor="center")
        self.tree.column("trigger", width=130, stretch=False)
        self.tree.column("replacement", width=360)
        self.tree.column("notes", width=220)
        self.tree.grid(row=2, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self.on_expansion_select)
        self.tree.bind("<Double-1>", lambda _event: self.load_selected_expansion())

        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar.grid(row=2, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        actions = ttk.Frame(parent)
        actions.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        actions.columnconfigure((0, 1, 2), weight=0)
        self._table_action_button(actions, "New", self.new_expansion).grid(row=0, column=0, padx=(0, 4), pady=(0, 4), sticky="w")
        self._table_action_button(actions, "Edit", self.load_selected_expansion).grid(row=0, column=1, padx=4, pady=(0, 4), sticky="w")
        self._table_action_button(actions, "Delete", self.delete_expansion).grid(row=0, column=2, padx=4, pady=(0, 4), sticky="w")
        self._table_action_button(actions, "Toggle Enabled", self.toggle_enabled).grid(row=1, column=0, columnspan=2, padx=(0, 4), sticky="w")

    def _table_action_button(self, parent: ttk.Frame, text: str, command: object) -> ttk.Button:
        return ttk.Button(
            parent,
            text=text,
            width=TABLE_ACTION_BUTTON_WIDTHS[text],
            command=command,
        )

    def _build_form(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(7, weight=1)

        ttk.Label(parent, text="Edit Expansion").grid(row=0, column=0, sticky="w")

        ttk.Label(parent, text="Section").grid(row=1, column=0, sticky="w", pady=(12, 2))
        self.section_combo = ttk.Combobox(parent, textvariable=self.section_var, state="readonly")
        self.section_combo.grid(row=2, column=0, sticky="ew")

        ttk.Label(parent, text="Trigger").grid(row=3, column=0, sticky="w", pady=(12, 2))
        ttk.Entry(parent, textvariable=self.trigger_var).grid(row=4, column=0, sticky="ew")

        ttk.Label(parent, text="Replacement text").grid(row=5, column=0, sticky="sw", pady=(12, 2))
        template_actions = ttk.Frame(parent)
        template_actions.grid(row=6, column=0, sticky="ew", pady=(0, 4))
        self._template_action_button(template_actions, "Insert Date/Time", self.insert_date_time).grid(row=0, column=0, padx=(0, 4), pady=(0, 4), sticky="w")
        self._template_action_button(template_actions, "Insert Input Box", self.insert_input_box).grid(row=0, column=1, padx=4, pady=(0, 4), sticky="w")
        self._template_action_button(template_actions, "Insert List Selection", self.insert_list_selection).grid(row=1, column=0, padx=(0, 4), pady=(0, 4), sticky="w")
        self._template_action_button(template_actions, "Insert Tab", self.insert_tab).grid(row=1, column=1, padx=4, pady=(0, 4), sticky="w")
        self._template_action_button(template_actions, "Insert Image", self.insert_image).grid(row=2, column=0, padx=(0, 4), sticky="w")

        self.replacement_text = tk.Text(parent, height=10, wrap=tk.WORD, undo=True)
        self.replacement_text.grid(row=7, column=0, sticky="nsew")

        ttk.Label(parent, text="Notes").grid(row=8, column=0, sticky="w", pady=(12, 2))
        self.notes_text = tk.Text(parent, height=5, wrap=tk.WORD, undo=True)
        self.notes_text.grid(row=9, column=0, sticky="ew")

        ttk.Checkbutton(parent, text="Enabled", variable=self.enabled_var).grid(row=10, column=0, sticky="w", pady=(10, 0))

        form_actions = ttk.Frame(parent)
        form_actions.grid(row=11, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(form_actions, text="Apply", command=self.apply_form).pack(side=tk.LEFT)
        ttk.Button(form_actions, text="Reset", command=self.new_expansion).pack(side=tk.LEFT, padx=4)

    def _template_action_button(self, parent: ttk.Frame, text: str, command: object) -> ttk.Button:
        return ttk.Button(
            parent,
            text=text,
            width=TEMPLATE_ACTION_BUTTON_WIDTHS[text],
            command=command,
        )

    def refresh_sections(self) -> None:
        self.section_list.delete(0, tk.END)
        for section in self.store.sections:
            self.section_list.insert(tk.END, section)

        selected = self.selected_section.get()
        if selected not in self.store.sections:
            selected = self.store.sections[0]
            self.selected_section.set(selected)
        self.section_list.selection_clear(0, tk.END)
        self.section_list.selection_set(self.store.sections.index(selected))
        self.section_list.see(self.store.sections.index(selected))
        self.section_combo.configure(values=self.store.sections)
        self.section_var.set(selected)

    def refresh_expansions(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

        query = self.search_var.get().strip().lower()
        section = self.selected_section.get()
        for index, expansion in enumerate(self.store.expansions):
            if not self._matches_filter(expansion, section, query):
                continue
            self.tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    "Yes" if expansion.enabled else "No",
                    expansion.trigger,
                    self._preview(expansion.replacement),
                    self._preview(expansion.notes),
                ),
            )

        duplicate_count = len(self.store.duplicate_triggers())
        self.duplicate_label.configure(
            text=f"Duplicate trigger groups: {duplicate_count}" if duplicate_count else ""
        )

    def _matches_filter(self, expansion: Expansion, section: str, query: str) -> bool:
        if expansion.section != section:
            return False
        if not query:
            return True
        haystack = " ".join(
            [expansion.section, expansion.trigger, expansion.replacement, expansion.notes]
        ).lower()
        return query in haystack

    def _preview(self, text: str, limit: int = 90) -> str:
        clean_text = " ".join(text.split())
        return clean_text if len(clean_text) <= limit else clean_text[: limit - 3] + "..."

    def on_section_select(self, _event: tk.Event) -> None:
        selection = self.section_list.curselection()
        if not selection:
            return
        section = self.section_list.get(selection[0])
        self.selected_section.set(section)
        self.section_var.set(section)
        self.current_expansion = None
        self.refresh_expansions()
        self.clear_form(keep_section=True)

    def on_expansion_select(self, _event: tk.Event) -> None:
        self.load_selected_expansion()

    def selected_expansion_index(self) -> int | None:
        selection = self.tree.selection()
        if not selection:
            return None
        return int(selection[0])

    def add_section(self) -> None:
        name = simpledialog.askstring("Add section", "Section name:", parent=self)
        if name is None:
            return
        try:
            self.store.add_section(name)
        except ValueError as exc:
            messagebox.showerror("Section error", str(exc))
            return
        self.selected_section.set(name.strip())
        self.refresh_sections()
        self.refresh_expansions()
        self.set_status(f'Added section "{name.strip()}".')

    def rename_section(self) -> None:
        old_name = self.selected_section.get()
        new_name = simpledialog.askstring("Rename section", "New section name:", initialvalue=old_name, parent=self)
        if new_name is None:
            return
        try:
            self.store.rename_section(old_name, new_name)
        except ValueError as exc:
            messagebox.showerror("Section error", str(exc))
            return
        self.selected_section.set(new_name.strip())
        self.refresh_sections()
        self.refresh_expansions()
        self.set_status(f'Renamed section to "{new_name.strip()}".')

    def delete_section(self) -> None:
        section = self.selected_section.get()
        count = sum(1 for expansion in self.store.expansions if expansion.section == section)
        if not messagebox.askyesno("Delete section", f'Delete "{section}" and {count} expansion(s)?'):
            return
        self.store.delete_section(section)
        self.selected_section.set(self.store.sections[0])
        self.refresh_sections()
        self.refresh_expansions()
        self.clear_form()
        self.set_status(f'Deleted section "{section}".')

    def new_expansion(self) -> None:
        self.current_expansion = None
        self.clear_form(keep_section=True)
        self.tree.selection_remove(self.tree.selection())

    def load_selected_expansion(self) -> None:
        index = self.selected_expansion_index()
        if index is None:
            return
        expansion = self.store.expansions[index]
        self.current_expansion = expansion
        self.section_var.set(expansion.section)
        self.trigger_var.set(expansion.trigger)
        self.enabled_var.set(expansion.enabled)
        self.replacement_text.delete("1.0", tk.END)
        self.replacement_text.insert("1.0", expansion.replacement)
        self.notes_text.delete("1.0", tk.END)
        self.notes_text.insert("1.0", expansion.notes)

    def insert_date_time(self) -> None:
        dialog = DateTimeDialog(self)
        if dialog.result:
            self.insert_replacement_snippet(dialog.result)

    def insert_input_box(self) -> None:
        dialog = InputPlaceholderDialog(self)
        if dialog.result:
            self.insert_replacement_snippet(dialog.result)

    def insert_list_selection(self) -> None:
        dialog = SelectPlaceholderDialog(self)
        if dialog.result:
            self.insert_replacement_snippet(dialog.result)

    def insert_tab(self) -> None:
        self.insert_replacement_snippet("{AHK_KEY:Tab}")

    def insert_image(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Choose image to insert",
            filetypes=IMAGE_FILE_TYPES,
        )
        if not file_path:
            return
        self.insert_replacement_snippet(f"{{AHK_IMAGE:{file_path}}}")

    def insert_replacement_snippet(self, snippet: str) -> None:
        self.replacement_text.insert(tk.INSERT, snippet)
        self.replacement_text.focus_set()

    def apply_form(self) -> None:
        try:
            expansion = self.read_form()
        except ValueError as exc:
            messagebox.showerror("Expansion error", str(exc))
            return

        if self.current_expansion is None:
            self.store.expansions.append(expansion)
            self.current_expansion = expansion
            self.set_status(f'Added trigger "{expansion.trigger}".')
        else:
            self.current_expansion.section = expansion.section
            self.current_expansion.trigger = expansion.trigger
            self.current_expansion.replacement = expansion.replacement
            self.current_expansion.enabled = expansion.enabled
            self.current_expansion.notes = expansion.notes
            self.set_status(f'Updated trigger "{expansion.trigger}".')

        self.selected_section.set(expansion.section)
        self.refresh_sections()
        self.refresh_expansions()
        self.warn_if_duplicate(expansion.trigger)

    def read_form(self) -> Expansion:
        section = self.section_var.get().strip()
        trigger = self.trigger_var.get().strip()
        replacement = self.replacement_text.get("1.0", "end-1c")
        notes = self.notes_text.get("1.0", "end-1c").strip()

        if not section:
            raise ValueError("Choose a section.")
        if not trigger:
            raise ValueError("Trigger cannot be blank.")
        if "::" in trigger or any(char.isspace() for char in trigger):
            raise ValueError('Trigger cannot contain whitespace or "::".')
        if not replacement:
            raise ValueError("Replacement text cannot be blank.")
        try:
            parse_replacement_template(replacement)
        except ValueError as exc:
            raise ValueError(f"Replacement placeholder is invalid: {exc}") from exc

        return Expansion(section, trigger, replacement, self.enabled_var.get(), notes)

    def delete_expansion(self) -> None:
        index = self.selected_expansion_index()
        if index is None:
            messagebox.showinfo("Delete expansion", "Select an expansion first.")
            return
        expansion = self.store.expansions[index]
        if not messagebox.askyesno("Delete expansion", f'Delete trigger "{expansion.trigger}"?'):
            return
        del self.store.expansions[index]
        self.current_expansion = None
        self.refresh_expansions()
        self.clear_form(keep_section=True)
        self.set_status(f'Deleted trigger "{expansion.trigger}".')

    def toggle_enabled(self) -> None:
        index = self.selected_expansion_index()
        if index is None:
            messagebox.showinfo("Toggle enabled", "Select an expansion first.")
            return
        expansion = self.store.expansions[index]
        expansion.enabled = not expansion.enabled
        self.refresh_expansions()
        self.set_status(f'{"Enabled" if expansion.enabled else "Disabled"} "{expansion.trigger}".')

    def current_ahk_path(self) -> Path:
        configured_path = self.ahk_path_var.get().strip()
        if not configured_path:
            return AHK_PATH
        return Path(configured_path).expanduser()

    def browse_ahk_path(self) -> None:
        current_path = self.current_ahk_path()
        file_path = filedialog.asksaveasfilename(
            title="Choose generated AutoHotkey script path",
            initialdir=str(current_path.parent if current_path.parent.exists() else APP_DIR),
            initialfile=current_path.name or DEFAULT_AHK,
            defaultextension=".ahk",
            filetypes=[("AutoHotkey files", "*.ahk"), ("All files", "*.*")],
        )
        if not file_path:
            return
        self.ahk_path_var.set(file_path)
        self.save_settings()

    def save_settings(self) -> None:
        self.settings.generated_ahk_path = str(self.current_ahk_path())
        self.ahk_path_var.set(self.settings.generated_ahk_path)
        try:
            self.settings.save(SETTINGS_PATH)
        except OSError as exc:
            messagebox.showerror("Settings error", f"Could not save {SETTINGS_PATH.name}: {exc}")
            return
        self.set_status(f"Saved {SETTINGS_PATH.name}.")

    def save_json(self) -> None:
        try:
            self.store.save(JSON_PATH)
        except OSError as exc:
            messagebox.showerror("Save error", f"Could not save {JSON_PATH.name}: {exc}")
            return
        self.set_status(f"Saved {JSON_PATH.name}.")

    def generate_ahk(self) -> None:
        ahk_path = self.current_ahk_path()
        try:
            self.save_settings()
            self.store.save(JSON_PATH)
            backup_path = generate_ahk(self.store, ahk_path, backup=True)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Generate error", str(exc))
            return

        message = f"Generated {ahk_path}."
        if backup_path:
            message += f" Backup: {backup_path.name}."
        self.set_status(message)
        messagebox.showinfo("Generate .ahk", message)

    def run_ahk(self) -> None:
        if self.ahk_process is not None and self.ahk_process.poll() is None:
            messagebox.showinfo("Run AHK", "The generated AHK script is already running from this app.")
            return

        try:
            self.ahk_process = self._launch_ahk()
        except ValueError as exc:
            messagebox.showerror("Run AHK", str(exc))
            return
        self.set_status(f"Running {self.current_ahk_path()}.")

    def reload_ahk(self) -> None:
        if self.ahk_process is None:
            messagebox.showinfo(
                "Reload AHK",
                "This app has not started the generated AHK script in this session. "
                "Only app-started processes can be cleanly reloaded.",
            )
            return

        if self.ahk_process.poll() is None:
            self.ahk_process.terminate()
            try:
                self.ahk_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.ahk_process.kill()
                self.ahk_process.wait(timeout=5)
        else:
            messagebox.showinfo(
                "Reload AHK",
                "The AHK process started by this app is no longer running. Use Run AHK to start it again.",
            )
            self.ahk_process = None
            return

        try:
            self.ahk_process = self._launch_ahk()
        except ValueError as exc:
            messagebox.showerror("Reload AHK", str(exc))
            return
        self.set_status(f"Reloaded {self.current_ahk_path()}.")

    def _launch_ahk(self) -> subprocess.Popen:
        ahk_path = self.current_ahk_path()
        if not ahk_path.exists():
            raise ValueError(f"{ahk_path} does not exist. Generate the .ahk file first.")

        executable = self._find_autohotkey()
        if executable is None:
            raise ValueError(
                "AutoHotkey was not found. Install AutoHotkey v2 or add AutoHotkey.exe to PATH."
            )

        try:
            return subprocess.Popen([str(executable), str(ahk_path)])
        except OSError as exc:
            raise ValueError(f"Could not launch AutoHotkey: {exc}") from exc

    def _find_autohotkey(self) -> Path | None:
        for name in ("AutoHotkey64.exe", "AutoHotkey.exe"):
            found = shutil.which(name)
            if found:
                return Path(found)

        candidates = [
            Path("C:/Program Files/AutoHotkey/v2/AutoHotkey64.exe"),
            Path("C:/Program Files/AutoHotkey/v2/AutoHotkey.exe"),
            Path("C:/Program Files/AutoHotkey/AutoHotkey64.exe"),
            Path("C:/Program Files/AutoHotkey/AutoHotkey.exe"),
            Path("C:/Program Files (x86)/AutoHotkey/AutoHotkey.exe"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def import_ahk(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Import AutoHotkey file",
            filetypes=[("AutoHotkey files", "*.ahk"), ("All files", "*.*")],
        )
        if not file_path:
            return

        try:
            imported = import_ahk(Path(file_path))
        except ValueError as exc:
            messagebox.showerror("Import error", str(exc))
            return

        conflict_count = count_import_conflicts(self.store, imported)
        conflict_action = "skip"
        if conflict_count:
            dialog = ImportConflictDialog(self, conflict_count)
            conflict_action = dialog.result
            if conflict_action is None:
                return

        result = merge_imported_store(self.store, imported, conflict_action)
        self.selected_section.set(imported.sections[0] if imported.sections else self.store.sections[0])
        self.current_expansion = None
        self.refresh_sections()
        self.refresh_expansions()
        self.clear_form()
        self.set_status(
            "Imported "
            f"{result.total_changed} expansion(s): "
            f"{result.added} added, {result.overwritten} overwritten, "
            f"{result.renamed} renamed, {result.skipped} skipped."
        )

    def clear_search(self) -> None:
        self.search_var.set("")
        self.refresh_expansions()

    def clear_form(self, keep_section: bool = False) -> None:
        if not keep_section:
            self.section_var.set(self.selected_section.get())
        self.trigger_var.set("")
        self.enabled_var.set(True)
        self.replacement_text.delete("1.0", tk.END)
        self.notes_text.delete("1.0", tk.END)

    def warn_if_duplicate(self, trigger: str) -> None:
        duplicates = self.store.duplicate_triggers()
        if trigger.lower() in duplicates:
            sections = ", ".join(expansion.section for expansion in duplicates[trigger.lower()])
            messagebox.showwarning(
                "Duplicate trigger",
                f'Trigger "{trigger}" appears in multiple expansions: {sections}.',
            )

    def set_status(self, message: str) -> None:
        self.status_var.set(message)


if __name__ == "__main__":
    app = ExpansionApp()
    app.mainloop()
