import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ahk_manager import (
    DEFAULT_AHK,
    DEFAULT_JSON,
    DEFAULT_SETTINGS,
    AppSettings,
    Expansion,
    ExpansionStore,
    TemplateDef,
    VariableDef,
    VARIABLE_TYPES,
    count_import_conflicts,
    generate_ahk,
    import_ahk,
    merge_imported_store,
    parse_replacement_template,
    resolve_expansion_preview,
    resolve_template_preview,
    resolve_template_segments,
    resolve_variable_preview,
    resolve_variable_segments,
    validate_template,
    validate_variable,
)


# Console programs (powershell, taskkill) each flash a command-prompt window
# when the frozen, windowed app shells out to them. CREATE_NO_WINDOW suppresses
# that; it only exists on Windows, so fall back to 0 elsewhere.
if sys.platform == "win32":
    _NO_WINDOW = subprocess.CREATE_NO_WINDOW
else:
    _NO_WINDOW = 0


def _app_dir() -> Path:
    # When frozen by PyInstaller, __file__ points at a temp extraction folder
    # that is wiped on exit. Store data next to the executable instead so the
    # user's expansions and settings persist (portable-app layout).
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resource_path(name: str) -> Path:
    # Bundled read-only resources (e.g. the icon) live in the PyInstaller
    # extraction dir when frozen, separate from the writable data dir.
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / name
    return Path(__file__).resolve().parent / name


APP_DIR = _app_dir()
ICON_PATH = _resource_path("app.ico")
JSON_PATH = APP_DIR / DEFAULT_JSON
AHK_PATH = APP_DIR / DEFAULT_AHK
SETTINGS_PATH = APP_DIR / DEFAULT_SETTINGS
UI_PREFS_PATH = APP_DIR / "ui_prefs.json"

# Qt file-dialog filter strings (";;"-separated) rather than tkinter tuples.
IMAGE_FILE_FILTER = (
    "Image files (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;"
    "PNG files (*.png);;"
    "JPEG files (*.jpg *.jpeg);;"
    "GIF files (*.gif);;"
    "Bitmap files (*.bmp);;"
    "WebP files (*.webp);;"
    "All files (*.*)"
)
AHK_FILE_FILTER = "AutoHotkey files (*.ahk);;All files (*.*)"
AHK_PROCESS_NAMES = {"autohotkey.exe", "autohotkey64.exe", "autohotkey32.exe"}


def has_reserved_placeholder_chars(value: str) -> bool:
    return any(char in value for char in "{}|")


def normalized_path_for_compare(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(path))))


def command_line_references_script(command_line: str, target_path: Path | str) -> bool:
    target = normalized_path_for_compare(target_path)
    for candidate in extract_ahk_script_paths(command_line):
        if normalized_path_for_compare(candidate) == target:
            return True
    return False


def extract_ahk_script_paths(command_line: str) -> list[str]:
    paths: list[str] = []
    quoted_re = re.compile(r'"([^"]+?\.ahk)"', re.IGNORECASE)
    for match in quoted_re.finditer(command_line):
        paths.append(match.group(1))

    stripped = quoted_re.sub(" ", command_line)
    for token in re.findall(r"[^\s]+?\.ahk", stripped, re.IGNORECASE):
        paths.append(token.strip('"'))
    return paths


# ---------------------------------------------------------------------------
# Theming
# ---------------------------------------------------------------------------

_THEME_COLORS = {
    "light": {
        "bg": "#f4f5f7",
        "panel": "#ffffff",
        "panel_alt": "#eceef1",
        "text": "#1f2937",
        "muted": "#6b7280",
        "border": "#d1d5db",
        "accent": "#2563eb",
        "accent_text": "#ffffff",
        "sidebar": "#e9ebef",
        "sidebar_sel": "#2563eb",
        "sidebar_sel_text": "#ffffff",
        "warn": "#9a3412",
        "selection": "#dbeafe",
    },
    "dark": {
        "bg": "#1e1f22",
        "panel": "#2b2d31",
        "panel_alt": "#232428",
        "text": "#e5e7eb",
        "muted": "#9ca3af",
        "border": "#3f4147",
        "accent": "#3b82f6",
        "accent_text": "#ffffff",
        "sidebar": "#191a1d",
        "sidebar_sel": "#3b82f6",
        "sidebar_sel_text": "#ffffff",
        "warn": "#fca5a5",
        "selection": "#1e3a5f",
    },
}


def build_stylesheet(theme: str) -> str:
    c = _THEME_COLORS[theme]
    return f"""
    QWidget {{
        background-color: {c['bg']};
        color: {c['text']};
        font-size: 10pt;
    }}
    QLabel {{ background: transparent; }}
    QLabel#Heading {{ font-size: 12pt; font-weight: 600; }}
    QLabel#Warn {{ color: {c['warn']}; }}
    QLabel#Muted {{ color: {c['muted']}; }}
    QListWidget, QTableWidget, QPlainTextEdit, QLineEdit, QComboBox {{
        background-color: {c['panel']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        selection-background-color: {c['selection']};
        selection-color: {c['text']};
    }}
    QPlainTextEdit, QLineEdit {{ padding: 4px 6px; }}
    QComboBox {{ padding: 4px 6px; }}
    QTableWidget {{ gridline-color: {c['border']}; }}
    QHeaderView::section {{
        background-color: {c['panel_alt']};
        color: {c['muted']};
        border: none;
        border-bottom: 1px solid {c['border']};
        padding: 5px 6px;
        font-weight: 600;
    }}
    QListWidget#Sidebar {{
        background-color: {c['sidebar']};
        border: none;
        border-right: 1px solid {c['border']};
        border-radius: 0px;
        outline: 0;
        padding-top: 8px;
    }}
    QListWidget#Sidebar::item {{
        padding: 10px 14px;
        margin: 2px 6px;
        border-radius: 6px;
    }}
    QListWidget#Sidebar::item:selected {{
        background-color: {c['sidebar_sel']};
        color: {c['sidebar_sel_text']};
    }}
    QPushButton {{
        background-color: {c['panel']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        padding: 6px 12px;
    }}
    QPushButton:hover {{ background-color: {c['panel_alt']}; }}
    QPushButton:pressed {{ background-color: {c['accent']}; color: {c['accent_text']}; }}
    QPushButton#Primary {{
        background-color: {c['accent']};
        color: {c['accent_text']};
        border: 1px solid {c['accent']};
        font-weight: 600;
    }}
    QPushButton#Primary:hover {{ background-color: {c['accent']}; }}
    QRadioButton, QCheckBox {{
        background: transparent;
        spacing: 8px;
    }}
    QRadioButton::indicator, QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {c['border']};
        background-color: {c['panel']};
    }}
    QRadioButton::indicator {{ border-radius: 9px; }}
    QCheckBox::indicator {{ border-radius: 4px; }}
    QRadioButton::indicator:hover, QCheckBox::indicator:hover {{
        border-color: {c['accent']};
    }}
    QRadioButton::indicator:checked {{
        border: 1px solid {c['accent']};
        background-color: qradialgradient(cx:0.5, cy:0.5, radius:0.5,
            fx:0.5, fy:0.5,
            stop:0 {c['accent']}, stop:0.5 {c['accent']},
            stop:0.55 {c['panel']}, stop:1 {c['panel']});
    }}
    QCheckBox::indicator:checked {{
        border: 1px solid {c['accent']};
        background-color: {c['accent']};
    }}
    QSplitter::handle {{ background-color: {c['border']}; }}
    QStatusBar {{ background: transparent; }}
    """


def detect_system_theme() -> str:
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
    except Exception:
        pass
    return "light"


def load_theme_pref() -> str | None:
    try:
        data = json.loads(UI_PREFS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    theme = data.get("theme")
    return theme if theme in ("light", "dark") else None


def save_theme_pref(theme: str) -> None:
    try:
        UI_PREFS_PATH.write_text(
            json.dumps({"theme": theme}, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Small message-box helpers
# ---------------------------------------------------------------------------

def show_error(parent, title: str, message: str) -> None:
    QMessageBox.critical(parent, title, message)


def show_info(parent, title: str, message: str) -> None:
    QMessageBox.information(parent, title, message)


def show_warning(parent, title: str, message: str) -> None:
    QMessageBox.warning(parent, title, message)


def confirm(parent, title: str, message: str) -> bool:
    reply = QMessageBox.question(
        parent,
        title,
        message,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return reply == QMessageBox.StandardButton.Yes


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

class ImportConflictDialog(QDialog):
    def __init__(self, parent, conflict_count: int) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import conflicts")
        self.result: str | None = None

        layout = QVBoxLayout(self)
        label = QLabel(
            f"{conflict_count} imported trigger(s) already exist in the same section. "
            "Choose how to handle all conflicts."
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        self._skip = QRadioButton("Skip duplicate triggers")
        self._overwrite = QRadioButton("Overwrite existing expansions")
        self._rename = QRadioButton("Keep both with renamed trigger")
        self._skip.setChecked(True)
        for widget in (self._skip, self._overwrite, self._rename):
            layout.addWidget(widget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        if self._overwrite.isChecked():
            self.result = "overwrite"
        elif self._rename.isChecked():
            self.result = "rename"
        else:
            self.result = "skip"
        super().accept()


class DateTimeDialog(QDialog):
    FORMAT_OPTIONS = {
        "Short date": "MM/dd/yyyy",
        "ISO date": "yyyy-MM-dd",
        "Long date": "dddd, MMMM d, yyyy",
        "Time": "h:mm tt",
        "Date + time": "yyyy-MM-dd h:mm tt",
        "Custom format": "",
    }

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setWindowTitle("Insert Date/Time")
        self.result: str | None = None

        layout = QGridLayout(self)
        layout.addWidget(QLabel("Format"), 0, 0)
        self._combo = QComboBox()
        self._combo.addItems(list(self.FORMAT_OPTIONS.keys()))
        self._combo.setCurrentText("ISO date")
        layout.addWidget(self._combo, 0, 1)
        layout.addWidget(QLabel("Custom"), 1, 0)
        self._custom = QLineEdit()
        layout.addWidget(self._custom, 1, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, 2, 0, 1, 2)

    def _date_format(self) -> str:
        selected = self._combo.currentText()
        if selected == "Custom format":
            return self._custom.text().strip()
        return self.FORMAT_OPTIONS[selected]

    def accept(self) -> None:
        date_format = self._date_format()
        if not date_format:
            show_error(self, "Date/Time format", "Enter a custom date/time format.")
            return
        if any(char in date_format for char in '{}"'):
            show_error(self, "Date/Time format", "Format cannot contain braces or double quotes.")
            return
        self.result = f'{{AHK_EXPR:FormatTime(A_Now, "{date_format}")}}'
        super().accept()


class InputPlaceholderDialog(QDialog):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setWindowTitle("Insert Input Box")
        self.result: str | None = None

        layout = QGridLayout(self)
        self._variable = QLineEdit("name")
        self._prompt = QLineEdit("Enter value")
        self._title = QLineEdit("Input")
        self._default = QLineEdit()
        fields = [
            ("Variable name", self._variable),
            ("Prompt text", self._prompt),
            ("Window title", self._title),
            ("Default value", self._default),
        ]
        for row, (label, widget) in enumerate(fields):
            layout.addWidget(QLabel(label), row, 0)
            widget.setMinimumWidth(240)
            layout.addWidget(widget, row, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, len(fields), 0, 1, 2)

    def _placeholder(self) -> str:
        return (
            "{AHK_INPUT:"
            f"{self._variable.text().strip()}|"
            f"{self._prompt.text().strip()}|"
            f"{self._title.text().strip()}|"
            f"{self._default.text().strip()}"
            "}"
        )

    def accept(self) -> None:
        fields = [self._prompt.text(), self._title.text(), self._default.text()]
        if any(has_reserved_placeholder_chars(value) for value in fields):
            show_error(
                self,
                "Input Box placeholder",
                "Prompt, title, and default value cannot contain braces or pipe characters.",
            )
            return
        try:
            parse_replacement_template(self._placeholder())
        except ValueError as exc:
            show_error(self, "Input Box placeholder", str(exc))
            return
        self.result = self._placeholder()
        super().accept()


class SelectPlaceholderDialog(QDialog):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setWindowTitle("Insert List Selection")
        self.result: str | None = None

        layout = QGridLayout(self)
        self._variable = QLineEdit("choice")
        self._prompt = QLineEdit("Choose an option")
        self._title = QLineEdit("Selection")
        fields = [
            ("Variable name", self._variable),
            ("Prompt text", self._prompt),
            ("Window title", self._title),
        ]
        for row, (label, widget) in enumerate(fields):
            layout.addWidget(QLabel(label), row, 0)
            widget.setMinimumWidth(240)
            layout.addWidget(widget, row, 1)

        layout.addWidget(QLabel("Options"), 3, 0, Qt.AlignmentFlag.AlignTop)
        self._options = QPlainTextEdit("Option A\nOption B\nOption C")
        self._options.setMinimumHeight(120)
        layout.addWidget(self._options, 3, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, 4, 0, 1, 2)

    def _placeholder(self) -> str:
        options = [
            line.strip()
            for line in self._options.toPlainText().splitlines()
            if line.strip()
        ]
        return (
            "{AHK_SELECT:"
            f"{self._variable.text().strip()}|"
            f"{self._prompt.text().strip()}|"
            f"{self._title.text().strip()}|"
            f"{'||'.join(options)}"
            "}"
        )

    def accept(self) -> None:
        fields = [self._prompt.text(), self._title.text()]
        if any(has_reserved_placeholder_chars(value) for value in fields):
            show_error(
                self,
                "List Selection placeholder",
                "Prompt and title cannot contain braces or pipe characters.",
            )
            return
        if any(char in self._options.toPlainText() for char in "{}|"):
            show_error(
                self,
                "List Selection placeholder",
                "Options cannot contain braces or pipe characters.",
            )
            return
        try:
            parse_replacement_template(self._placeholder())
        except ValueError as exc:
            show_error(self, "List Selection placeholder", str(exc))
            return
        self.result = self._placeholder()
        super().accept()


class LibrarySelectionDialog(QDialog):
    def __init__(self, parent, title: str, items: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.result: str | None = None

        layout = QVBoxLayout(self)
        self._list = QListWidget()
        self._list.addItems(items)
        if items:
            self._list.setCurrentRow(0)
        self._list.itemDoubleClicked.connect(lambda _item: self.accept())
        self._list.setMinimumSize(300, 220)
        layout.addWidget(self._list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        item = self._list.currentItem()
        if item is None:
            show_error(self, "Selection", "Select an item first.")
            return
        self.result = item.text()
        super().accept()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

# Insertion toolbar entries: (button label, handler attribute name). The label
# drops the repetitive "Insert " prefix (kept as a tooltip) so the toolbar stays
# compact and the form panels fit in a reasonably sized window.
INSERTION_ACTIONS = (
    ("Date/Time", "insert_date_time"),
    ("Input Box", "insert_input_box"),
    ("List Selection", "insert_list_selection"),
    ("Tab", "insert_tab"),
    ("Image", "insert_image"),
    ("Variable", "insert_variable"),
    ("Template", "insert_template"),
)


class PreviewDialog(QDialog):
    def __init__(self, parent, title: str, content: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 620)
        self.setMinimumSize(520, 360)

        layout = QVBoxLayout(self)
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(QFont("Consolas", 10))
        self._text.setPlainText(content)
        layout.addWidget(self._text)

        buttons = QDialogButtonBox()
        copy_button = buttons.addButton(
            "Copy to Clipboard", QDialogButtonBox.ButtonRole.ActionRole
        )
        copy_button.clicked.connect(self._copy_to_clipboard)
        close_button = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        close_button.clicked.connect(self.accept)
        layout.addWidget(buttons)

    def _copy_to_clipboard(self) -> None:
        QGuiApplication.clipboard().setText(self._text.toPlainText())


class ExpansionApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("AutoHotkey Text Expansion Manager")
        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        # Open wide enough that the Snippets table shows all columns without a
        # horizontal scrollbar (even when a vertical scrollbar is present).
        self.resize(1448, 720)

        self.store = self._load_store()
        self.settings = self._load_settings()
        self.ahk_process: subprocess.Popen | None = None
        self.theme = load_theme_pref() or detect_system_theme()

        self.selected_section = self.store.sections[0]
        self.current_expansion: Expansion | None = None
        self.current_variable: VariableDef | None = None
        self.current_template: TemplateDef | None = None

        self._build_ui()
        self.apply_theme()
        self.refresh_sections()
        self.refresh_expansions()
        self.refresh_variables()
        self.refresh_templates()

        # Derive the window's minimum size from what the widest page actually
        # needs so panels can never be shrunk into overlapping each other.
        # (A hardcoded minimum that is smaller than the content disables Qt's
        # layout-driven minimum and lets the panes clip.)
        content_min = self.centralWidget().minimumSizeHint()
        self.setMinimumSize(content_min.width(), max(content_min.height(), 600))

    # -- loading -----------------------------------------------------------
    def _load_store(self) -> ExpansionStore:
        try:
            return ExpansionStore.load(JSON_PATH)
        except ValueError as exc:
            show_error(None, "Load error", str(exc))
            return ExpansionStore()

    def _load_settings(self) -> AppSettings:
        try:
            return AppSettings.load(SETTINGS_PATH, AHK_PATH)
        except ValueError as exc:
            show_error(None, "Settings error", str(exc))
            return AppSettings(str(AHK_PATH))

    # -- UI construction ---------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        body.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_expansions_page())
        self.stack.addWidget(self._build_variables_page())
        self.stack.addWidget(self._build_templates_page())
        body.addWidget(self.stack, 1)

        root.addLayout(body, 1)
        root.addWidget(self._build_footer())

        self.nav.setCurrentRow(0)

    def _build_sidebar(self) -> QWidget:
        container = QWidget()
        container.setFixedWidth(168)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setObjectName("Sidebar")
        for label in ("⌨  Expansions", "ƒ  Variables", "▤  Templates"):
            QListWidgetItem(label, self.nav)
        self.nav.currentRowChanged.connect(self.stack_set_index)
        layout.addWidget(self.nav, 1)

        self.theme_button = QPushButton()
        self.theme_button.clicked.connect(self.toggle_theme)
        theme_wrap = QWidget()
        theme_wrap.setObjectName("Sidebar")
        wrap_layout = QVBoxLayout(theme_wrap)
        wrap_layout.setContentsMargins(10, 6, 10, 10)
        wrap_layout.addWidget(self.theme_button)
        layout.addWidget(theme_wrap)
        return container

    def stack_set_index(self, index: int) -> None:
        if index >= 0:
            self.stack.setCurrentIndex(index)

    def _build_expansions_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_sections_panel())
        splitter.addWidget(self._build_table_panel())
        splitter.addWidget(self._build_form_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 5)
        splitter.setStretchFactor(2, 3)
        splitter.setSizes([232, 540, 360])
        layout.addWidget(splitter)
        return page

    def _build_sections_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(232)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 10, 0)

        heading = QLabel("Sections")
        heading.setObjectName("Heading")
        layout.addWidget(heading)

        self.section_list = QListWidget()
        self.section_list.currentRowChanged.connect(self.on_section_select)
        layout.addWidget(self.section_list, 1)

        # One compact row: tighter padding and a trailing stretch keep the three
        # buttons at their natural size (not stretched) while staying legible.
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        for text, slot in (
            ("Add", self.add_section),
            ("Rename", self.rename_section),
            ("Delete", self.delete_section),
        ):
            button = QPushButton(text)
            button.setStyleSheet("padding: 6px 4px;")
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return panel

    def _build_table_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(340)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 0, 10, 0)

        heading = QLabel("Snippets")
        heading.setObjectName("Heading")
        layout.addWidget(heading)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search"))
        self.search_edit = QLineEdit()
        self.search_edit.textChanged.connect(lambda _text: self.refresh_expansions())
        search_row.addWidget(self.search_edit, 1)
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self.clear_search)
        search_row.addWidget(clear_button)
        layout.addLayout(search_row)

        self.duplicate_label = QLabel("")
        self.duplicate_label.setObjectName("Warn")
        layout.addWidget(self.duplicate_label)

        self.tree = QTableWidget(0, 4)
        self.tree.setHorizontalHeaderLabels(["On", "Trigger", "Replacement", "Notes"])
        self._configure_table(self.tree)
        header = self.tree.horizontalHeader()
        self.tree.setColumnWidth(0, 44)
        self.tree.setColumnWidth(1, 130)
        self.tree.setColumnWidth(2, 300)
        header.setStretchLastSection(True)
        self.tree.itemSelectionChanged.connect(self.on_expansion_select)
        self.tree.cellDoubleClicked.connect(lambda _r, _c: self.load_selected_expansion())
        layout.addWidget(self.tree, 1)

        actions = QHBoxLayout()
        for text, slot in (
            ("New", self.new_expansion),
            ("Edit", self.load_selected_expansion),
            ("Delete", self.delete_expansion),
            ("Toggle Enabled", self.toggle_enabled),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        return panel

    def _build_form_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(272)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 0, 0, 0)

        heading = QLabel("Edit Expansion")
        heading.setObjectName("Heading")
        layout.addWidget(heading)

        layout.addWidget(QLabel("Section"))
        self.section_combo = QComboBox()
        layout.addWidget(self.section_combo)

        layout.addWidget(QLabel("Trigger"))
        self.trigger_edit = QLineEdit()
        layout.addWidget(self.trigger_edit)

        layout.addWidget(QLabel("Replacement text"))
        toolbar = self._build_insertion_toolbar(lambda: self.replacement_text)
        layout.addWidget(toolbar)
        self.replacement_text = QPlainTextEdit()
        layout.addWidget(self.replacement_text, 1)

        layout.addWidget(QLabel("Notes"))
        self.notes_text = QPlainTextEdit()
        self.notes_text.setMaximumHeight(90)
        layout.addWidget(self.notes_text)

        self.enabled_check = QCheckBox("Enabled")
        self.enabled_check.setChecked(True)
        layout.addWidget(self.enabled_check)

        form_actions = QHBoxLayout()
        apply_button = QPushButton("Apply")
        apply_button.setObjectName("Primary")
        apply_button.clicked.connect(self.apply_form)
        reset_button = QPushButton("Reset")
        reset_button.clicked.connect(self.new_expansion)
        preview_button = QPushButton("Preview Expansion")
        preview_button.clicked.connect(self.preview_expansion)
        form_actions.addWidget(apply_button)
        form_actions.addWidget(reset_button)
        form_actions.addWidget(preview_button)
        form_actions.addStretch(1)
        layout.addLayout(form_actions)
        return panel

    def _build_insertion_toolbar(self, target_getter) -> QWidget:
        toolbar = QWidget()
        grid = QGridLayout(toolbar)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        for index, (label, handler_name) in enumerate(INSERTION_ACTIONS):
            command = getattr(self, handler_name)
            button = QPushButton(label)
            button.setToolTip(f"Insert {label}")
            button.setStyleSheet("padding: 6px 8px;")
            button.clicked.connect(
                lambda _checked=False, command=command, getter=target_getter: command(getter())
            )
            grid.addWidget(button, index // 2, index % 2)
        return toolbar

    def _build_variables_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(12, 12, 12, 12)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 10, 0)
        heading = QLabel("Variables")
        heading.setObjectName("Heading")
        left_layout.addWidget(heading)
        self.variable_tree = QTableWidget(0, 3)
        self.variable_tree.setHorizontalHeaderLabels(["Type", "Name", "Prompt"])
        self._configure_table(self.variable_tree)
        self.variable_tree.setColumnWidth(0, 110)
        self.variable_tree.setColumnWidth(1, 130)
        self.variable_tree.horizontalHeader().setStretchLastSection(True)
        self.variable_tree.itemSelectionChanged.connect(self.on_variable_select)
        left_layout.addWidget(self.variable_tree, 1)
        var_actions = QHBoxLayout()
        for text, slot in (("New", self.new_variable), ("Delete", self.delete_variable)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            var_actions.addWidget(button)
        var_actions.addStretch(1)
        left_layout.addLayout(var_actions)

        form = QWidget()
        form_layout = QGridLayout(form)
        form_layout.setContentsMargins(10, 0, 0, 0)
        self.variable_name_edit = QLineEdit()
        self.variable_type_combo = QComboBox()
        self.variable_type_combo.addItems(sorted(VARIABLE_TYPES))
        self.variable_prompt_edit = QLineEdit()
        self.variable_default_edit = QLineEdit()
        form_layout.addWidget(QLabel("Name"), 0, 0)
        form_layout.addWidget(self.variable_name_edit, 0, 1)
        form_layout.addWidget(QLabel("Type"), 1, 0)
        form_layout.addWidget(self.variable_type_combo, 1, 1)
        form_layout.addWidget(QLabel("Prompt text"), 2, 0)
        form_layout.addWidget(self.variable_prompt_edit, 2, 1)
        form_layout.addWidget(QLabel("Default/format"), 3, 0)
        form_layout.addWidget(self.variable_default_edit, 3, 1)
        form_layout.addWidget(QLabel("List options"), 4, 0, Qt.AlignmentFlag.AlignTop)
        self.variable_options_text = QPlainTextEdit()
        self.variable_options_text.setMaximumHeight(120)
        form_layout.addWidget(self.variable_options_text, 4, 1)
        form_layout.addWidget(QLabel("Notes"), 5, 0, Qt.AlignmentFlag.AlignTop)
        self.variable_notes_text = QPlainTextEdit()
        form_layout.addWidget(self.variable_notes_text, 5, 1)
        apply_button = QPushButton("Apply Variable")
        apply_button.setObjectName("Primary")
        apply_button.clicked.connect(self.apply_variable)
        preview_button = QPushButton("Preview Variable")
        preview_button.clicked.connect(self.preview_variable)
        variable_actions = QHBoxLayout()
        variable_actions.addWidget(apply_button)
        variable_actions.addWidget(preview_button)
        variable_actions.addStretch(1)
        form_layout.addLayout(variable_actions, 6, 1)
        form_layout.setRowStretch(5, 1)
        form_layout.setColumnStretch(1, 1)

        splitter.addWidget(left)
        splitter.addWidget(form)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([360, 620])
        outer.addWidget(splitter)
        return page

    def _build_templates_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(12, 12, 12, 12)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 10, 0)
        heading = QLabel("Templates")
        heading.setObjectName("Heading")
        left_layout.addWidget(heading)
        self.template_tree = QTableWidget(0, 2)
        self.template_tree.setHorizontalHeaderLabels(["Name", "Description"])
        self._configure_table(self.template_tree)
        self.template_tree.setColumnWidth(0, 130)
        self.template_tree.horizontalHeader().setStretchLastSection(True)
        self.template_tree.itemSelectionChanged.connect(self.on_template_select)
        left_layout.addWidget(self.template_tree, 1)
        tpl_actions = QHBoxLayout()
        for text, slot in (
            ("New", self.new_template),
            ("Duplicate", self.duplicate_template),
            ("Delete", self.delete_template),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            tpl_actions.addWidget(button)
        tpl_actions.addStretch(1)
        left_layout.addLayout(tpl_actions)

        form = QWidget()
        form_layout = QGridLayout(form)
        form_layout.setContentsMargins(10, 0, 0, 0)
        self.template_name_edit = QLineEdit()
        self.template_description_edit = QLineEdit()
        form_layout.addWidget(QLabel("Name"), 0, 0)
        form_layout.addWidget(self.template_name_edit, 0, 1)
        form_layout.addWidget(QLabel("Description"), 1, 0)
        form_layout.addWidget(self.template_description_edit, 1, 1)
        form_layout.addWidget(QLabel("Body"), 2, 0, Qt.AlignmentFlag.AlignTop)
        toolbar = self._build_insertion_toolbar(lambda: self.template_body_text)
        form_layout.addWidget(toolbar, 2, 1)
        self.template_body_text = QPlainTextEdit()
        form_layout.addWidget(self.template_body_text, 3, 1)
        form_layout.addWidget(QLabel("Notes"), 4, 0, Qt.AlignmentFlag.AlignTop)
        self.template_notes_text = QPlainTextEdit()
        self.template_notes_text.setMaximumHeight(90)
        form_layout.addWidget(self.template_notes_text, 4, 1)
        apply_button = QPushButton("Apply Template")
        apply_button.setObjectName("Primary")
        apply_button.clicked.connect(self.apply_template)
        preview_button = QPushButton("Preview Template")
        preview_button.clicked.connect(self.preview_template)
        template_actions = QHBoxLayout()
        template_actions.addWidget(apply_button)
        template_actions.addWidget(preview_button)
        template_actions.addStretch(1)
        form_layout.addLayout(template_actions, 5, 1)
        form_layout.setRowStretch(3, 1)
        form_layout.setColumnStretch(1, 1)

        splitter.addWidget(left)
        splitter.addWidget(form)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([360, 620])
        outer.addWidget(splitter)
        return page

    def _build_footer(self) -> QWidget:
        footer = QFrame()
        footer.setObjectName("Footer")
        outer = QVBoxLayout(footer)
        outer.setContentsMargins(12, 8, 12, 10)
        outer.setSpacing(8)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Generated AHK path"))
        self.ahk_path_edit = QLineEdit(self.settings.generated_ahk_path)
        self.ahk_path_edit.editingFinished.connect(self.save_settings)
        path_row.addWidget(self.ahk_path_edit, 1)
        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(self.browse_ahk_path)
        path_row.addWidget(browse_button)
        outer.addLayout(path_row)

        action_row = QHBoxLayout()
        self.status_label = QLabel("Ready.")
        self.status_label.setObjectName("Muted")
        action_row.addWidget(self.status_label, 1)
        for text, slot, primary in (
            ("Save JSON", self.save_json, False),
            ("Generate && Run AHK", self.generate_and_run_ahk, True),
            ("Run AHK", self.run_ahk, False),
            ("Import .ahk", self.import_ahk, False),
        ):
            button = QPushButton(text)
            if primary:
                button.setObjectName("Primary")
            button.clicked.connect(slot)
            action_row.addWidget(button)
        outer.addLayout(action_row)
        return footer

    @staticmethod
    def _configure_table(table: QTableWidget) -> None:
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setWordWrap(False)

    # -- theming -----------------------------------------------------------
    def apply_theme(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_stylesheet(self.theme))
        self.theme_button.setText(
            "☀  Light mode" if self.theme == "dark" else "☾  Dark mode"
        )
        self._apply_titlebar_theme()

    def _apply_titlebar_theme(self) -> None:
        """Match the native Windows title bar to the current theme (Win 10/11)."""
        if sys.platform != "win32":
            return
        try:
            import ctypes

            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            hwnd = int(self.winId())
            value = ctypes.c_int(1 if self.theme == "dark" else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
            # Nudge the non-client area to repaint so the change shows immediately
            # while the window is already visible.
            if self.isVisible():
                SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_FRAMECHANGED = 0x2, 0x1, 0x4, 0x20
                ctypes.windll.user32.SetWindowPos(
                    hwnd, 0, 0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
                )
        except Exception:
            pass

    def toggle_theme(self) -> None:
        self.theme = "light" if self.theme == "dark" else "dark"
        save_theme_pref(self.theme)
        self.apply_theme()

    # -- table helpers -----------------------------------------------------
    @staticmethod
    def _table_selected_store_index(table: QTableWidget) -> int | None:
        rows = table.selectionModel().selectedRows()
        if not rows:
            return None
        item = table.item(rows[0].row(), 0)
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    # -- refresh -----------------------------------------------------------
    def refresh_sections(self) -> None:
        self.section_list.blockSignals(True)
        self.section_list.clear()
        self.section_list.addItems(self.store.sections)
        self.section_list.blockSignals(False)

        selected = self.selected_section
        if selected not in self.store.sections:
            selected = self.store.sections[0]
            self.selected_section = selected
        index = self.store.sections.index(selected)
        self.section_list.blockSignals(True)
        self.section_list.setCurrentRow(index)
        self.section_list.blockSignals(False)

        self.section_combo.blockSignals(True)
        self.section_combo.clear()
        self.section_combo.addItems(self.store.sections)
        self.section_combo.setCurrentText(selected)
        self.section_combo.blockSignals(False)

    def refresh_expansions(self) -> None:
        query = self.search_edit.text().strip().lower()
        section = self.selected_section
        self.tree.blockSignals(True)
        self.tree.setRowCount(0)
        for index, expansion in enumerate(self.store.expansions):
            if not self._matches_filter(expansion, section, query):
                continue
            row = self.tree.rowCount()
            self.tree.insertRow(row)
            values = (
                "Yes" if expansion.enabled else "No",
                expansion.trigger,
                self._preview(expansion.replacement),
                self._preview(expansion.notes),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, index)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.tree.setItem(row, column, item)
        self.tree.blockSignals(False)

        duplicate_count = len(self.store.duplicate_triggers())
        self.duplicate_label.setText(
            f"Duplicate trigger groups: {duplicate_count}" if duplicate_count else ""
        )

    def refresh_variables(self) -> None:
        if not hasattr(self, "variable_tree"):
            return
        self.variable_tree.blockSignals(True)
        self.variable_tree.setRowCount(0)
        for index, variable in enumerate(self.store.variables):
            self.variable_tree.insertRow(index)
            type_item = QTableWidgetItem(variable.type)
            type_item.setData(Qt.ItemDataRole.UserRole, index)
            self.variable_tree.setItem(index, 0, type_item)
            self.variable_tree.setItem(index, 1, QTableWidgetItem(variable.name))
            self.variable_tree.setItem(index, 2, QTableWidgetItem(variable.prompt_text))
        self.variable_tree.blockSignals(False)

    def refresh_templates(self) -> None:
        if not hasattr(self, "template_tree"):
            return
        self.template_tree.blockSignals(True)
        self.template_tree.setRowCount(0)
        for index, template in enumerate(self.store.templates):
            self.template_tree.insertRow(index)
            name_item = QTableWidgetItem(template.name)
            name_item.setData(Qt.ItemDataRole.UserRole, index)
            self.template_tree.setItem(index, 0, name_item)
            self.template_tree.setItem(index, 1, QTableWidgetItem(template.description))
        self.template_tree.blockSignals(False)

    # -- variables ---------------------------------------------------------
    def on_variable_select(self) -> None:
        index = self._table_selected_store_index(self.variable_tree)
        if index is None:
            return
        variable = self.store.variables[index]
        self.current_variable = variable
        self.variable_name_edit.setText(variable.name)
        self.variable_type_combo.setCurrentText(variable.type)
        self.variable_prompt_edit.setText(variable.prompt_text)
        self.variable_default_edit.setText(variable.default_value)
        self.variable_options_text.setPlainText("\n".join(variable.list_options))
        self.variable_notes_text.setPlainText(variable.notes)

    def on_template_select(self) -> None:
        index = self._table_selected_store_index(self.template_tree)
        if index is None:
            return
        template = self.store.templates[index]
        self.current_template = template
        self.template_name_edit.setText(template.name)
        self.template_description_edit.setText(template.description)
        self.template_body_text.setPlainText(template.body)
        self.template_notes_text.setPlainText(template.notes)

    def new_variable(self) -> None:
        self.current_variable = None
        self.variable_name_edit.clear()
        self.variable_type_combo.setCurrentText("text_input")
        self.variable_prompt_edit.clear()
        self.variable_default_edit.clear()
        self.variable_options_text.clear()
        self.variable_notes_text.clear()

    def new_template(self) -> None:
        self.current_template = None
        self.template_name_edit.clear()
        self.template_description_edit.clear()
        self.template_body_text.clear()
        self.template_notes_text.clear()

    def read_variable_form(self) -> VariableDef:
        variable = VariableDef(
            name=self.variable_name_edit.text().strip(),
            type=self.variable_type_combo.currentText().strip(),
            prompt_text=self.variable_prompt_edit.text().strip(),
            default_value=self.variable_default_edit.text().strip(),
            list_options=[
                line.strip()
                for line in self.variable_options_text.toPlainText().splitlines()
                if line.strip()
            ],
            notes=self.variable_notes_text.toPlainText().strip(),
        )
        validate_variable(variable)
        return variable

    def read_template_form(self) -> TemplateDef:
        template = TemplateDef(
            name=self.template_name_edit.text().strip(),
            description=self.template_description_edit.text().strip(),
            body=self.template_body_text.toPlainText(),
            notes=self.template_notes_text.toPlainText().strip(),
        )
        validate_template(template)
        parse_replacement_template(template.body)
        return template

    def apply_variable(self) -> None:
        try:
            variable = self.read_variable_form()
            self.ensure_unique_variable_name(variable.name, self.current_variable)
        except ValueError as exc:
            show_error(self, "Variable error", str(exc))
            return
        if self.current_variable is None:
            self.store.variables.append(variable)
            self.current_variable = variable
        else:
            self.current_variable.name = variable.name
            self.current_variable.type = variable.type
            self.current_variable.prompt_text = variable.prompt_text
            self.current_variable.default_value = variable.default_value
            self.current_variable.list_options = variable.list_options
            self.current_variable.notes = variable.notes
        self.refresh_variables()
        self.set_status(f'Saved variable "{variable.name}".')

    def preview_variable(self) -> None:
        try:
            variable = self.read_variable_form()
            result = resolve_variable_preview(variable)
        except ValueError as exc:
            show_error(self, "Preview Variable", str(exc))
            return
        PreviewDialog(self, result.title, result.content).exec()

    def apply_template(self) -> None:
        try:
            template = self.read_template_form()
            self.ensure_unique_template_name(template.name, self.current_template)
            candidate_templates = [
                template if item is self.current_template else item
                for item in self.store.templates
            ]
            if self.current_template is None:
                candidate_templates.append(template)
            segments = resolve_template_segments(
                parse_replacement_template(template.body),
                candidate_templates,
                stack=(template.name,),
            )
            resolve_variable_segments(segments, self.store.variables)
        except ValueError as exc:
            show_error(self, "Template error", str(exc))
            return
        if self.current_template is None:
            self.store.templates.append(template)
            self.current_template = template
        else:
            self.current_template.name = template.name
            self.current_template.description = template.description
            self.current_template.body = template.body
            self.current_template.notes = template.notes
        self.refresh_templates()
        self.set_status(f'Saved template "{template.name}".')

    def preview_template(self) -> None:
        try:
            template = self.read_template_form()
            candidate_templates = [
                template if item is self.current_template else item
                for item in self.store.templates
            ]
            if self.current_template is None:
                candidate_templates.append(template)
            preview_store = ExpansionStore(
                sections=self.store.sections,
                expansions=self.store.expansions,
                variables=self.store.variables,
                templates=candidate_templates,
            )
            result = resolve_template_preview(template, preview_store)
        except ValueError as exc:
            show_error(self, "Preview Template", str(exc))
            return
        PreviewDialog(self, result.title, result.content).exec()

    def ensure_unique_variable_name(self, name: str, current: VariableDef | None) -> None:
        for variable in self.store.variables:
            if variable is not current and variable.name == name:
                raise ValueError(f'Duplicate variable name "{name}".')

    def ensure_unique_template_name(self, name: str, current: TemplateDef | None) -> None:
        for template in self.store.templates:
            if template is not current and template.name == name:
                raise ValueError(f'Duplicate template name "{name}".')

    def delete_variable(self) -> None:
        index = self._table_selected_store_index(self.variable_tree)
        if index is None:
            show_info(self, "Delete variable", "Select a variable first.")
            return
        variable = self.store.variables[index]
        if not confirm(self, "Delete variable", f'Delete variable "{variable.name}"?'):
            return
        self.store.variables.remove(variable)
        self.current_variable = None
        self.new_variable()
        self.refresh_variables()

    def delete_template(self) -> None:
        index = self._table_selected_store_index(self.template_tree)
        if index is None:
            show_info(self, "Delete template", "Select a template first.")
            return
        template = self.store.templates[index]
        if not confirm(self, "Delete template", f'Delete template "{template.name}"?'):
            return
        self.store.templates.remove(template)
        self.current_template = None
        self.new_template()
        self.refresh_templates()

    def duplicate_template(self) -> None:
        index = self._table_selected_store_index(self.template_tree)
        if index is None:
            show_info(self, "Duplicate template", "Select a template first.")
            return
        template = self.store.templates[index]
        base_name = f"{template.name} Copy"
        name = base_name
        suffix = 2
        while self.store.template_by_name(name):
            name = f"{base_name} {suffix}"
            suffix += 1
        copy = TemplateDef(name, template.description, template.body, template.notes)
        self.store.templates.append(copy)
        self.current_template = copy
        self.refresh_templates()
        self.template_tree.selectRow(len(self.store.templates) - 1)
        self.on_template_select()

    # -- expansion filtering / preview ------------------------------------
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

    # -- section handling --------------------------------------------------
    def on_section_select(self, row: int) -> None:
        if row < 0:
            return
        item = self.section_list.item(row)
        if item is None:
            return
        section = item.text()
        self.selected_section = section
        self.section_combo.setCurrentText(section)
        self.current_expansion = None
        self.refresh_expansions()
        self.clear_form(keep_section=True)

    def on_expansion_select(self) -> None:
        self.load_selected_expansion()

    def selected_expansion_index(self) -> int | None:
        return self._table_selected_store_index(self.tree)

    def add_section(self) -> None:
        name, ok = QInputDialog.getText(self, "Add section", "Section name:")
        if not ok:
            return
        try:
            self.store.add_section(name)
        except ValueError as exc:
            show_error(self, "Section error", str(exc))
            return
        self.selected_section = name.strip()
        self.refresh_sections()
        self.refresh_expansions()
        self.set_status(f'Added section "{name.strip()}".')

    def rename_section(self) -> None:
        old_name = self.selected_section
        new_name, ok = QInputDialog.getText(
            self, "Rename section", "New section name:", text=old_name
        )
        if not ok:
            return
        try:
            self.store.rename_section(old_name, new_name)
        except ValueError as exc:
            show_error(self, "Section error", str(exc))
            return
        self.selected_section = new_name.strip()
        self.refresh_sections()
        self.refresh_expansions()
        self.set_status(f'Renamed section to "{new_name.strip()}".')

    def delete_section(self) -> None:
        section = self.selected_section
        count = sum(1 for expansion in self.store.expansions if expansion.section == section)
        if not confirm(self, "Delete section", f'Delete "{section}" and {count} expansion(s)?'):
            return
        self.store.delete_section(section)
        self.selected_section = self.store.sections[0]
        self.refresh_sections()
        self.refresh_expansions()
        self.clear_form()
        self.set_status(f'Deleted section "{section}".')

    def new_expansion(self) -> None:
        self.current_expansion = None
        self.clear_form(keep_section=True)
        self.tree.clearSelection()

    def load_selected_expansion(self) -> None:
        index = self.selected_expansion_index()
        if index is None:
            return
        expansion = self.store.expansions[index]
        self.current_expansion = expansion
        self.section_combo.setCurrentText(expansion.section)
        self.trigger_edit.setText(expansion.trigger)
        self.enabled_check.setChecked(expansion.enabled)
        self.replacement_text.setPlainText(expansion.replacement)
        self.notes_text.setPlainText(expansion.notes)

    # -- insertion actions -------------------------------------------------
    def insert_date_time(self, target: QPlainTextEdit | None = None) -> None:
        dialog = DateTimeDialog(self)
        if dialog.exec() and dialog.result:
            self.insert_snippet(dialog.result, target)

    def insert_input_box(self, target: QPlainTextEdit | None = None) -> None:
        dialog = InputPlaceholderDialog(self)
        if dialog.exec() and dialog.result:
            self.insert_snippet(dialog.result, target)

    def insert_list_selection(self, target: QPlainTextEdit | None = None) -> None:
        dialog = SelectPlaceholderDialog(self)
        if dialog.exec() and dialog.result:
            self.insert_snippet(dialog.result, target)

    def insert_tab(self, target: QPlainTextEdit | None = None) -> None:
        self.insert_snippet("{AHK_KEY:Tab}", target)

    def insert_image(self, target: QPlainTextEdit | None = None) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Choose image to insert", "", IMAGE_FILE_FILTER
        )
        if not file_path:
            return
        self.insert_snippet(f"{{AHK_IMAGE:{file_path}}}", target)

    def insert_variable(self, target: QPlainTextEdit | None = None) -> None:
        names = [variable.name for variable in self.store.variables]
        if not names:
            show_info(self, "Insert Variable", "Create a variable first.")
            return
        dialog = LibrarySelectionDialog(self, "Insert Variable", names)
        if dialog.exec() and dialog.result:
            self.insert_snippet(f"{{VAR:{dialog.result}}}", target)

    def insert_template(self, target: QPlainTextEdit | None = None) -> None:
        names = [template.name for template in self.store.templates]
        if not names:
            show_info(self, "Insert Template", "Create a template first.")
            return
        dialog = LibrarySelectionDialog(self, "Insert Template", names)
        if dialog.exec() and dialog.result:
            self.insert_snippet(f"{{TPL:{dialog.result}}}", target)

    def insert_replacement_snippet(self, snippet: str) -> None:
        self.insert_snippet(snippet, self.replacement_text)

    def insert_snippet(self, snippet: str, target: QPlainTextEdit | None = None) -> None:
        target = target or self.replacement_text
        target.insertPlainText(snippet)
        target.setFocus()

    # -- expansion form ----------------------------------------------------
    def apply_form(self) -> None:
        try:
            expansion = self.read_form()
        except ValueError as exc:
            show_error(self, "Expansion error", str(exc))
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

        self.selected_section = expansion.section
        self.refresh_sections()
        self.refresh_expansions()
        self.warn_if_duplicate(expansion.trigger)

    def preview_expansion(self) -> None:
        try:
            expansion = self.read_form()
            result = resolve_expansion_preview(expansion, self.store)
        except ValueError as exc:
            show_error(self, "Preview Expansion", str(exc))
            return
        PreviewDialog(self, result.title, result.content).exec()

    def read_form(self) -> Expansion:
        section = self.section_combo.currentText().strip()
        trigger = self.trigger_edit.text().strip()
        replacement = self.replacement_text.toPlainText()
        notes = self.notes_text.toPlainText().strip()

        if not section:
            raise ValueError("Choose a section.")
        if not trigger:
            raise ValueError("Trigger cannot be blank.")
        if "::" in trigger or any(char.isspace() for char in trigger):
            raise ValueError('Trigger cannot contain whitespace or "::".')
        if not replacement:
            raise ValueError("Replacement text cannot be blank.")
        try:
            segments = resolve_template_segments(parse_replacement_template(replacement), self.store.templates)
            resolve_variable_segments(segments, self.store.variables)
        except ValueError as exc:
            raise ValueError(f"Replacement placeholder is invalid: {exc}") from exc

        return Expansion(section, trigger, replacement, self.enabled_check.isChecked(), notes)

    def delete_expansion(self) -> None:
        index = self.selected_expansion_index()
        if index is None:
            show_info(self, "Delete expansion", "Select an expansion first.")
            return
        expansion = self.store.expansions[index]
        if not confirm(self, "Delete expansion", f'Delete trigger "{expansion.trigger}"?'):
            return
        del self.store.expansions[index]
        self.current_expansion = None
        self.refresh_expansions()
        self.clear_form(keep_section=True)
        self.set_status(f'Deleted trigger "{expansion.trigger}".')

    def toggle_enabled(self) -> None:
        index = self.selected_expansion_index()
        if index is None:
            show_info(self, "Toggle enabled", "Select an expansion first.")
            return
        expansion = self.store.expansions[index]
        expansion.enabled = not expansion.enabled
        self.refresh_expansions()
        self.set_status(f'{"Enabled" if expansion.enabled else "Disabled"} "{expansion.trigger}".')

    # -- AHK path / settings ----------------------------------------------
    def current_ahk_path(self) -> Path:
        configured_path = self.ahk_path_edit.text().strip()
        if not configured_path:
            return AHK_PATH
        return Path(configured_path).expanduser()

    def browse_ahk_path(self) -> None:
        current_path = self.current_ahk_path()
        initial_dir = str(current_path.parent if current_path.parent.exists() else APP_DIR)
        initial = str(Path(initial_dir) / (current_path.name or DEFAULT_AHK))
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Choose generated AutoHotkey script path",
            initial,
            AHK_FILE_FILTER,
        )
        if not file_path:
            return
        self.ahk_path_edit.setText(file_path)
        self.save_settings()

    def save_settings(self) -> None:
        self.settings.generated_ahk_path = str(self.current_ahk_path())
        self.ahk_path_edit.setText(self.settings.generated_ahk_path)
        try:
            self.settings.save(SETTINGS_PATH)
        except OSError as exc:
            show_error(self, "Settings error", f"Could not save {SETTINGS_PATH.name}: {exc}")
            return
        self.set_status(f"Saved {SETTINGS_PATH.name}.")

    def save_json(self) -> None:
        try:
            self.store.save(JSON_PATH)
        except OSError as exc:
            show_error(self, "Save error", f"Could not save {JSON_PATH.name}: {exc}")
            return
        self.set_status(f"Saved {JSON_PATH.name}.")

    def generate_and_run_ahk(self) -> None:
        ahk_path = self.current_ahk_path()
        try:
            self.save_settings()
            self.store.save(JSON_PATH)
            backup_path = generate_ahk(self.store, ahk_path, backup=True)
        except (OSError, ValueError) as exc:
            show_error(self, "Generate & Run AHK", str(exc))
            return

        terminated = 0
        inspect_warning = ""
        try:
            terminated = self._terminate_matching_ahk_processes(ahk_path)
            self.ahk_process = self._launch_ahk()
        except ProcessLookupError as exc:
            inspect_warning = str(exc)
            try:
                self.ahk_process = self._launch_ahk()
            except ValueError as launch_exc:
                show_error(
                    self,
                    "Generate & Run AHK",
                    f"{inspect_warning}\n\nAlso failed to launch: {launch_exc}",
                )
                return
        except ValueError as exc:
            show_error(self, "Generate & Run AHK", str(exc))
            return

        message = f"Generated and ran {ahk_path}."
        if backup_path:
            message += f" Backup: {backup_path.name}."
        if terminated:
            message += f" Stopped {terminated} matching running script process(es)."
        elif inspect_warning:
            message += f" Warning: {inspect_warning}"
        self.set_status(message)
        show_info(self, "Generate & Run AHK", message)

    def run_ahk(self) -> None:
        if self.ahk_process is not None and self.ahk_process.poll() is None:
            show_info(self, "Run AHK", "The generated AHK script is already running from this app.")
            return

        try:
            self.ahk_process = self._launch_ahk()
        except ValueError as exc:
            show_error(self, "Run AHK", str(exc))
            return
        self.set_status(f"Running {self.current_ahk_path()}.")

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
            return subprocess.Popen([str(executable), str(ahk_path)], creationflags=_NO_WINDOW)
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

    def _terminate_matching_ahk_processes(self, ahk_path: Path) -> int:
        processes = self._running_autohotkey_processes()
        target_path = ahk_path.resolve(strict=False)
        current_pid = os.getpid()
        terminated = 0

        for process in processes:
            pid = process.get("ProcessId")
            command_line = process.get("CommandLine") or ""
            name = str(process.get("Name") or "").lower()
            if name not in AHK_PROCESS_NAMES or not isinstance(pid, int) or pid == current_pid:
                continue
            if not command_line_references_script(command_line, target_path):
                continue
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=True,
                    capture_output=True,
                    text=True,
                    creationflags=_NO_WINDOW,
                )
                terminated += 1
            except (OSError, subprocess.CalledProcessError) as exc:
                raise ValueError(f"Could not stop matching AutoHotkey process {pid}: {exc}") from exc
        return terminated

    def _running_autohotkey_processes(self) -> list[dict[str, object]]:
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Get-CimInstance Win32_Process "
                "| Where-Object { $_.Name -match '^AutoHotkey(32|64)?\\.exe$' } "
                "| Select-Object ProcessId,Name,CommandLine "
                "| ConvertTo-Json -Compress"
            ),
        ]
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ProcessLookupError(
                "Could not inspect running AutoHotkey processes; launching without stopping any existing script."
            ) from exc

        output = result.stdout.strip()
        if not output:
            return []
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            raise ProcessLookupError(
                "Could not parse running AutoHotkey process list; launching without stopping any existing script."
            ) from exc
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    # -- import ------------------------------------------------------------
    def import_ahk(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Import AutoHotkey file", "", AHK_FILE_FILTER
        )
        if not file_path:
            return

        try:
            imported = import_ahk(Path(file_path))
        except ValueError as exc:
            show_error(self, "Import error", str(exc))
            return

        conflict_count = count_import_conflicts(self.store, imported)
        conflict_action = "skip"
        if conflict_count:
            dialog = ImportConflictDialog(self, conflict_count)
            if not dialog.exec():
                return
            conflict_action = dialog.result
            if conflict_action is None:
                return

        result = merge_imported_store(self.store, imported, conflict_action)
        self.selected_section = imported.sections[0] if imported.sections else self.store.sections[0]
        self.current_expansion = None
        self.refresh_sections()
        self.refresh_expansions()
        self.refresh_variables()
        self.refresh_templates()
        self.clear_form()
        status = (
            "Imported "
            f"{result.total_changed} expansion(s): "
            f"{result.added} added, {result.overwritten} overwritten, "
            f"{result.renamed} renamed, {result.skipped} skipped."
        )
        if result.variables_added or result.templates_added:
            status += (
                f" Added {result.variables_added} variable(s), "
                f"{result.templates_added} template(s)."
            )
        self.set_status(status)

    # -- misc --------------------------------------------------------------
    def clear_search(self) -> None:
        self.search_edit.clear()
        self.refresh_expansions()

    def clear_form(self, keep_section: bool = False) -> None:
        if not keep_section:
            self.section_combo.setCurrentText(self.selected_section)
        self.trigger_edit.clear()
        self.enabled_check.setChecked(True)
        self.replacement_text.clear()
        self.notes_text.clear()

    def warn_if_duplicate(self, trigger: str) -> None:
        duplicates = self.store.duplicate_triggers()
        if trigger in duplicates:
            sections = ", ".join(expansion.section for expansion in duplicates[trigger])
            show_warning(
                self,
                "Duplicate trigger",
                f'Trigger "{trigger}" appears in multiple expansions: {sections}.',
            )

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    window = ExpansionApp()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
