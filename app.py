import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from ahk_manager import DEFAULT_AHK, DEFAULT_JSON, Expansion, ExpansionStore, generate_ahk, import_ahk


APP_DIR = Path(__file__).resolve().parent
JSON_PATH = APP_DIR / DEFAULT_JSON
AHK_PATH = APP_DIR / DEFAULT_AHK


class ExpansionApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("AutoHotkey Text Expansion Manager")
        self.geometry("1120x680")
        self.minsize(900, 560)

        self.store = self._load_store()
        self.selected_section = tk.StringVar(value=self.store.sections[0])
        self.search_var = tk.StringVar()
        self.current_expansion: Expansion | None = None

        self.section_var = tk.StringVar()
        self.trigger_var = tk.StringVar()
        self.enabled_var = tk.BooleanVar(value=True)
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

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.grid(row=0, column=0, sticky="nsew")

        left = ttk.Frame(paned, padding=8)
        center = ttk.Frame(paned, padding=(8, 8, 4, 8))
        right = ttk.Frame(paned, padding=(4, 8, 8, 8))
        paned.add(left, weight=1)
        paned.add(center, weight=4)
        paned.add(right, weight=3)

        self._build_sections(left)
        self._build_table(center)
        self._build_form(right)

        footer = ttk.Frame(self, padding=(8, 0, 8, 8))
        footer.grid(row=1, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)

        ttk.Label(footer, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="Save JSON", command=self.save_json).grid(row=0, column=1, padx=4)
        ttk.Button(footer, text="Generate .ahk", command=self.generate_ahk).grid(row=0, column=2, padx=4)
        ttk.Button(footer, text="Import .ahk", command=self.import_ahk).grid(row=0, column=3, padx=4)

    def _build_sections(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        ttk.Label(parent, text="Sections").grid(row=0, column=0, sticky="w")
        self.section_list = tk.Listbox(parent, exportselection=False)
        self.section_list.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(6, 8))
        self.section_list.bind("<<ListboxSelect>>", self.on_section_select)

        ttk.Button(parent, text="Add", command=self.add_section).grid(row=2, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(parent, text="Rename", command=self.rename_section).grid(row=2, column=1, sticky="ew", padx=4)
        ttk.Button(parent, text="Delete", command=self.delete_section).grid(row=2, column=2, sticky="ew", padx=(4, 0))

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
        ttk.Button(actions, text="New", command=self.new_expansion).pack(side=tk.LEFT)
        ttk.Button(actions, text="Edit", command=self.load_selected_expansion).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Delete", command=self.delete_expansion).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Toggle Enabled", command=self.toggle_enabled).pack(side=tk.LEFT, padx=4)

    def _build_form(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(6, weight=1)

        ttk.Label(parent, text="Edit Expansion").grid(row=0, column=0, sticky="w")

        ttk.Label(parent, text="Section").grid(row=1, column=0, sticky="w", pady=(12, 2))
        self.section_combo = ttk.Combobox(parent, textvariable=self.section_var, state="readonly")
        self.section_combo.grid(row=2, column=0, sticky="ew")

        ttk.Label(parent, text="Trigger").grid(row=3, column=0, sticky="w", pady=(12, 2))
        ttk.Entry(parent, textvariable=self.trigger_var).grid(row=4, column=0, sticky="ew")

        ttk.Label(parent, text="Replacement text").grid(row=5, column=0, sticky="sw", pady=(12, 2))
        self.replacement_text = tk.Text(parent, height=10, wrap=tk.WORD, undo=True)
        self.replacement_text.grid(row=6, column=0, sticky="nsew")

        ttk.Label(parent, text="Notes").grid(row=7, column=0, sticky="w", pady=(12, 2))
        self.notes_text = tk.Text(parent, height=5, wrap=tk.WORD, undo=True)
        self.notes_text.grid(row=8, column=0, sticky="ew")

        ttk.Checkbutton(parent, text="Enabled", variable=self.enabled_var).grid(row=9, column=0, sticky="w", pady=(10, 0))

        form_actions = ttk.Frame(parent)
        form_actions.grid(row=10, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(form_actions, text="Apply", command=self.apply_form).pack(side=tk.LEFT)
        ttk.Button(form_actions, text="Reset", command=self.new_expansion).pack(side=tk.LEFT, padx=4)

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

    def save_json(self) -> None:
        try:
            self.store.save(JSON_PATH)
        except OSError as exc:
            messagebox.showerror("Save error", f"Could not save {JSON_PATH.name}: {exc}")
            return
        self.set_status(f"Saved {JSON_PATH.name}.")

    def generate_ahk(self) -> None:
        try:
            self.store.save(JSON_PATH)
            backup_path = generate_ahk(self.store, AHK_PATH, backup=True)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Generate error", str(exc))
            return

        message = f"Generated {AHK_PATH.name}."
        if backup_path:
            message += f" Backup: {backup_path.name}."
        self.set_status(message)
        messagebox.showinfo("Generate .ahk", message)

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

        if self.store.expansions:
            replace = messagebox.askyesno(
                "Import .ahk",
                "Replace current sections and expansions with the imported file?",
            )
            if not replace:
                return

        self.store = imported
        self.selected_section.set(self.store.sections[0])
        self.current_expansion = None
        self.refresh_sections()
        self.refresh_expansions()
        self.clear_form()
        self.set_status(f"Imported {len(imported.expansions)} expansion(s).")

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
