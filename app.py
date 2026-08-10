import json
import os
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QEvent, QItemSelectionModel, QObject, QRect, QSize, Qt
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QIcon,
    QPainter,
    QPixmap,
)
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
    QTableWidgetSelectionRange,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ahk_manager import (
    AHK_CONFIG_DIR_NAME,
    DEFAULT_AHK,
    DEFAULT_DATE_FORMAT,
    DEFAULT_JSON,
    BACKUP_RETENTION_LIMIT,
    DEFAULT_SETTINGS,
    AppSettings,
    Expansion,
    ExpansionStore,
    ImportConflicts,
    ReferenceKind,
    TemplateDef,
    VariableDef,
    VARIABLE_TYPES,
    backup_file,
    backup_timestamp,
    copy_store,
    count_import_conflicts,
    find_references,
    generate_ahk,
    import_ahk,
    list_backups,
    merge_imported_store,
    migrate_backups,
    restore_backup,
    parse_replacement_template,
    placeholder_problems,
    rename_in_text,
    rename_references,
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
# The app's own files sit in a folder of their own, leaving the executable and
# the generated script as the only two things loose in the install folder. The
# script looks for its icon in the same folder, relative to itself.
CONFIG_DIR = APP_DIR / AHK_CONFIG_DIR_NAME
JSON_PATH = CONFIG_DIR / DEFAULT_JSON
AHK_PATH = APP_DIR / DEFAULT_AHK
SETTINGS_PATH = CONFIG_DIR / DEFAULT_SETTINGS
UI_PREFS_PATH = CONFIG_DIR / "ui_prefs.json"
# Backups are collected here rather than left beside the files they copy, which
# scattered them through the working folders -- including wherever the user had
# pointed the generated script, which may be a synced folder.
DEFAULT_BACKUP_DIR = APP_DIR / "backups"

# Shown in the Help page. No colours are set anywhere in this markup: the text
# has to stay readable in both themes, so it inherits the widget's palette.
HELP_HTML = f"""
<h3>What this does</h3>
<p>You keep a library of short <b>triggers</b> here. This app turns that library
into an AutoHotkey script, and while that script is running, typing a trigger in
any application replaces it with the matching text.</p>
<p>Nothing you type here takes effect until you press
<b>Generate &amp; Run AHK</b>, which rewrites <code>{DEFAULT_AHK}</code> and
restarts the running script.</p>

<h3>Expansions</h3>
<p>An expansion is a trigger plus its replacement text, filed under a section.
Sections are only for organising the list; they do not affect behaviour.</p>
<ul>
<li>A trigger cannot contain spaces or <code>::</code>.</li>
<li>Triggers are case sensitive, and a leading character such as
<code>;</code> makes accidental firing much less likely.</li>
<li>Clear the <b>On</b> box to keep an expansion but leave it out of the
generated script.</li>
<li>An expansion fires when you type its trigger followed by a space, a tab or
punctuation, and that character is kept: <code>;ty</code> and a space give
&quot;Thank you! &quot;. Tick <b>Drop the character that triggered it</b> to
have the expansion end exactly where its replacement does.</li>
<li>Double-click a row, or select it and press <b>Edit</b>, to open it in the
editor on the right. Ctrl-click and Shift-click select several rows at once,
which <b>Delete</b> and <b>Toggle On/Off</b> then act on together.</li>
<li>Duplicate triggers are flagged, and the last one generated wins.</li>
</ul>

<h3>Placeholders</h3>
<p>Replacement text can contain placeholders that are filled in at the moment
the expansion fires. Use the buttons under the replacement box to insert them
rather than typing the syntax by hand.</p>
<ul>
<li><code>{{AHK_INPUT:...}}</code> asks for a value in a text field.</li>
<li><code>{{AHK_SELECT:...}}</code> offers a dropdown of fixed choices.</li>
<li><code>{{AHK_EXPR:...}}</code> inserts a date or time, evaluated as it
fires.</li>
<li><code>{{AHK_KEY:...}}</code> presses a key, such as Tab.</li>
<li><code>{{AHK_IMAGE:...}}</code> pastes an image from a file.</li>
<li><code>{{VAR:name}}</code> and <code>{{TPL:name}}</code> pull in a variable
or a template.</li>
</ul>
<p>When an expansion asks for anything, you get <b>one dialog</b> with a field
per question and a preview of the finished text above them that updates as you
type. A value asked for twice is asked for once and used in both places. The
dialog opens on the monitor you were typing on. Press Escape to cancel and
nothing is inserted.</p>

<h3>Variables and templates</h3>
<p>A <b>variable</b> is a named placeholder you define once and reuse by name.
A <b>template</b> is a named block of replacement text that can itself contain
variables and other templates. Both keep you from repeating the same definition
across many expansions; change the definition and every expansion using it
changes too.</p>

<h3>Saving and backups</h3>
<p>Every change is written to <code>{AHK_CONFIG_DIR_NAME}\\{DEFAULT_JSON}</code>
the moment you apply it, so there is no save step and closing the window cannot
lose work. Because of that, closing without saving is no longer a way to abandon
a session's edits.</p>
<p>That <b>{AHK_CONFIG_DIR_NAME}</b> folder, beside the app, holds everything
the app keeps: your library, your settings, and the icon the generated script
puts on its tray and its prompts. The script looks for that icon there first and
then next to itself, so a script copied to a machine without the app still runs
either way.</p>
<p>Instead, a copy of <code>{DEFAULT_JSON}</code> is kept the first time you
change anything in a session, and a copy of the generated script each time you
generate. Both live in a <b>backups</b> folder beside the app, and the
<b>Settings</b> page is where you change that location or restore from a
copy.</p>

<h3>Buttons along the bottom</h3>
<ul>
<li><b>Generate &amp; Run AHK</b> writes the script and restarts it. This is
the one to press after making changes.</li>
<li><b>Run AHK</b> starts the existing script without rewriting it.</li>
<li><b>Import .ahk</b> reads expansions out of an AutoHotkey file and merges
them in, asking what to do about any that clash.</li>
</ul>
"""

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

# Each variable type uses some of the value fields and ignores the rest, so the
# Variables form shows only the ones that apply and puts one of these notes
# where the box it dropped used to be. A fixed example date keeps the codes
# concrete without the reference looking out of date next year.
TEXT_INPUT_DEFAULT_NOTE = (
    "Default is optional. Leave it blank and the box opens empty."
)
LIST_SELECTION_DEFAULT_NOTE = (
    "The first list option below is the default. Put one option per line."
)
DATE_FORMAT_NOTE = (
    "Codes, shown for Tuesday 4 August 2026 at 2:30 PM:\n"
    "yyyy / yy  —  2026, 26\n"
    "MMMM / MMM / MM / M  —  August, Aug, 08, 8\n"
    "dddd / ddd / dd / d  —  Tuesday, Tue, 04, 4\n"
    "HH / hh  —  14, 02\n"
    "mm / ss / tt  —  30, 00, PM\n"
    "Punctuation and spaces pass through, so yyyy-MM-dd h:mm tt gives "
    "2026-08-04 2:30 PM.\n"
    f"Blank uses {DEFAULT_DATE_FORMAT}. Braces and double quotes are rejected."
)


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


_NAV_ICON_SIZE = 20

# Windows ships a monochrome icon font -- Fluent on 11, MDL2 on 10 -- whose
# glyphs take the painter's pen. The plain Unicode symbols do not: a keyboard
# and a gear resolve to the colour emoji font, which draws a bitmap that keeps
# its own colour in every theme and on the selected row. Those symbols stay as
# the fallback for a machine without either font.
_NAV_ICON_FONTS = ("Segoe Fluent Icons", "Segoe MDL2 Assets")

# (icon-font code point, fallback symbol, label). Glyph and label are kept
# apart so the glyph can be drawn in a column of its own; see nav_icon.
_NAV_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("", "⌨", "Expansions"),
    ("", "ƒ", "Variables"),
    ("", "▤", "Templates"),
    ("", "⚙", "Settings"),
    ("", "?", "Help"),
)


# Every dialog is its own top-level window, and Windows colours a title bar
# from a per-window attribute Qt knows nothing about, so theming the main
# window does nothing for the popups. _TitleBarThemeFilter applies it to each
# window as it is shown, reading the theme from here.
_titlebar_theme = "light"


def set_titlebar_theme(theme: str) -> None:
    global _titlebar_theme
    _titlebar_theme = theme


def apply_titlebar_theme(widget: QWidget, repaint: bool = False) -> None:
    """Colour a window's native title bar for the current theme (Win 10/11).

    Best effort throughout: the attribute is unsupported on older builds, where
    the call fails and the title bar simply stays light.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        # winId() creates the native window if it does not exist yet, which is
        # what lets this run before the window is mapped and so avoids showing
        # a light title bar for a frame first.
        hwnd = int(widget.winId())
        value = ctypes.c_int(1 if _titlebar_theme == "dark" else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        # Nudge the non-client area to repaint so a change shows immediately on
        # a window that is already on screen.
        if repaint and widget.isVisible():
            SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_FRAMECHANGED = 0x2, 0x1, 0x4, 0x20
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
            )
    except Exception:
        pass


class TitleBarThemeFilter(QObject):
    """Theme every window's title bar as it is shown.

    Installed on the application rather than called per dialog because the
    QMessageBox convenience statics build and run their dialog internally and
    never hand it back, so there is no other moment to reach it. Filtering the
    event also runs before the widget handles Show, while the window is still
    unmapped.
    """

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.Show
            and isinstance(watched, QWidget)
            and watched.isWindow()
        ):
            apply_titlebar_theme(watched)
        return False


@lru_cache(maxsize=1)
def _icon_font_family() -> str | None:
    """The first installed icon font, or None to fall back to plain symbols."""
    families = set(QFontDatabase.families())
    for name in _NAV_ICON_FONTS:
        if name in families:
            return name
    return None


def nav_icon(code_point: str, fallback: str, theme: str) -> QIcon:
    """Draw a sidebar glyph into an icon of fixed size.

    The symbols are very different widths -- a keyboard and a gear run to about
    twice a hooked f or a question mark -- so with them inline in the label
    text, padding could not line the labels up and every row started at its own
    x. Given to Qt as icons they get one fixed-width column and the text after
    them aligns. Both icon modes are drawn because the view asks for Selected on
    the current row, where the text colour changes as well.
    """
    colors = _THEME_COLORS[theme]
    family = _icon_font_family()
    glyph = code_point if family else fallback
    # Rendered above 1x so the glyph stays sharp where Windows is scaling.
    scale = 2
    icon = QIcon()
    for mode, color in (
        (QIcon.Mode.Normal, colors["text"]),
        (QIcon.Mode.Selected, colors["sidebar_sel_text"]),
    ):
        pixmap = QPixmap(_NAV_ICON_SIZE * scale, _NAV_ICON_SIZE * scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        pixmap.setDevicePixelRatio(scale)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        font = painter.font()
        if family:
            font.setFamily(family)
            font.setPointSizeF(12.0)
        else:
            font.setPointSizeF(11.0)
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(
            QRect(0, 0, _NAV_ICON_SIZE, _NAV_ICON_SIZE),
            Qt.AlignmentFlag.AlignCenter,
            glyph,
        )
        painter.end()
        icon.addPixmap(pixmap, mode)
    return icon


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
    QListWidget, QTableWidget, QPlainTextEdit, QTextBrowser, QLineEdit, QComboBox {{
        background-color: {c['panel']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        selection-background-color: {c['selection']};
        selection-color: {c['text']};
    }}
    QPlainTextEdit, QTextBrowser, QLineEdit {{ padding: 4px 6px; }}
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
    """The saved theme, or None to fall back to the system setting.

    Every unreadable shape gives the same answer rather than an error. This
    runs before the window exists -- so an exception here is a crash box with
    nothing behind it -- and unlike the library there is nothing here worth
    recovering: the file holds one preference, and the next theme toggle
    rewrites it. Losing it costs the user a click.

    A JSON array, string, number or null parses cleanly and then raises
    AttributeError on .get, which is what took the window down.
    """
    try:
        parsed = json.loads(UI_PREFS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    theme = parsed.get("theme")
    return theme if theme in ("light", "dark") else None


def migrate_config_files() -> tuple[list[str], list[str]]:
    """Move the app's files into the config folder, once, on first run.

    Returns what moved and what could not, for the status bar to report. A file
    already in the config folder wins: the one left outside is then a leftover,
    not the library, and moving it over the top would lose the real one.

    One folder, one decision, taken from where the library is configured to
    live. Deciding per file would let a caller that redirects only some of
    these paths -- every test fixture here redirects the library -- leave the
    rest pointing at the real install folder, and move the user's files during
    a test run.
    """
    config_dir = JSON_PATH.parent
    if config_dir.name != AHK_CONFIG_DIR_NAME:
        return [], []
    moved: list[str] = []
    failed: list[str] = []
    for path in (JSON_PATH, SETTINGS_PATH, UI_PREFS_PATH):
        legacy = config_dir.parent / path.name
        if path.parent != config_dir or path.exists() or not legacy.is_file():
            continue
        try:
            config_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy), str(path))
        except OSError:
            failed.append(legacy.name)
            continue
        moved.append(path.name)
    return moved, failed


def save_theme_pref(theme: str) -> None:
    try:
        UI_PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        UI_PREFS_PATH.write_text(
            json.dumps({"theme": theme}, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Small message-box helpers
# ---------------------------------------------------------------------------

def show_error(parent: QWidget | None, title: str, message: str) -> None:
    QMessageBox.critical(parent, title, message)


def show_info(parent: QWidget | None, title: str, message: str) -> None:
    QMessageBox.information(parent, title, message)


def show_warning(parent: QWidget | None, title: str, message: str) -> None:
    QMessageBox.warning(parent, title, message)


def item_count(count: int) -> str:
    """"1 item" or "N items", to be read as "... is used by <this>"."""
    return "1 item" if count == 1 else f"{count} items"


def reference_listing(users: list[str], limit: int = 12) -> str:
    """The affected items as dialog text, capped so a long list still fits.

    A library can reference one variable from dozens of expansions, and a
    message box that tall is unreadable and can run off the screen.
    """
    shown = [f"  - {user}" for user in users[:limit]]
    if len(users) > limit:
        shown.append(f"  - ... and {len(users) - limit} more")
    return "\n".join(shown)


def confirm(parent: QWidget | None, title: str, message: str) -> bool:
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
    def __init__(self, parent, conflicts: ImportConflicts) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import conflicts")
        self.choice: str | None = None

        layout = QVBoxLayout(self)
        # Counted separately because they read differently: a trigger clash
        # affects that one expansion, a definition clash can reach every
        # expansion already here that uses the name.
        described = []
        if conflicts.triggers:
            described.append(f"{conflicts.triggers} trigger(s) in the same section")
        if conflicts.definitions:
            described.append(f"{conflicts.definitions} variable(s) or template(s)")
        label = QLabel(
            f"The imported file has {' and '.join(described)} that already "
            "exist here. Choose how to handle all of them."
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        self._skip = QRadioButton("Skip them, keeping what is already here")
        self._overwrite = QRadioButton("Overwrite with the imported versions")
        self._rename = QRadioButton("Keep both, renaming what is imported")
        self._skip.setChecked(True)
        for widget in (self._skip, self._overwrite, self._rename):
            layout.addWidget(widget)

        if conflicts.definitions:
            # The one consequence that is not obvious from the choice itself.
            note = QLabel(
                "Overwriting a variable or template also changes every "
                "expansion already here that uses it. Renaming rewrites the "
                "imported expansions to use the renamed copies."
            )
            note.setObjectName("Muted")
            note.setWordWrap(True)
            layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        if self._overwrite.isChecked():
            self.choice = "overwrite"
        elif self._rename.isChecked():
            self.choice = "rename"
        else:
            self.choice = "skip"
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
        self.choice: str | None = None

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
        self.choice = f'{{AHK_EXPR:FormatTime(A_Now, "{date_format}")}}'
        super().accept()


class InputPlaceholderDialog(QDialog):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setWindowTitle("Insert Input Box")
        self.choice: str | None = None

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
        self.choice = self._placeholder()
        super().accept()


class SelectPlaceholderDialog(QDialog):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setWindowTitle("Insert List Selection")
        self.choice: str | None = None

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
        self.choice = self._placeholder()
        super().accept()


class LibrarySelectionDialog(QDialog):
    def __init__(self, parent, title: str, items: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.choice: str | None = None

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
        self.choice = item.text()
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

        # Set by _load_store, and read by persist before it writes over the
        # file it could not read. Declared first because _load_store runs here.
        self._store_unreadable = False
        # Only ever true after a write was refused: every edit otherwise
        # reaches disk as it is applied. Shown in the footer and checked on
        # close. _set_unsaved needs the footer, which does not exist yet.
        self._unsaved_changes = False
        # Before anything is read: these files used to sit loose beside the
        # executable, and loading first would find nothing and start empty with
        # the real library one folder up.
        self._config_migration = migrate_config_files()
        self.store = self._load_store()
        self.settings = self._load_settings()
        self.ahk_process: subprocess.Popen | None = None
        self.theme = load_theme_pref() or detect_system_theme()
        # One backup per session, taken before the first write. See _backup_once.
        self._session_backed_up = False
        self._active_backup_dir = self._resolve_backup_dir(
            self.settings.backup_directory
        )
        self._backup_dir_warned = False

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
        # After the UI exists, so the count can be reported in the status bar.
        self._migrate_legacy_backups()
        self._report_config_migration()

        # Derive the window's minimum size from what the widest page actually
        # needs so panels can never be shrunk into overlapping each other.
        # (A hardcoded minimum that is smaller than the content disables Qt's
        # layout-driven minimum and lets the panes clip.)
        content_min = self.centralWidget().minimumSizeHint()
        self.setMinimumSize(content_min.width(), max(content_min.height(), 600))

    # -- loading -----------------------------------------------------------
    def _load_store(self) -> ExpansionStore:
        """The library on disk, or an empty one if the file cannot be read.

        Opening anyway is deliberate -- restoring a backup is done from the
        Help page, which is unreachable if the window never opens. But the
        empty store that stands in is not the user's library, so the failure is
        recorded: see persist, which will not write over the unread file
        without being told to.
        """
        try:
            store = ExpansionStore.load(JSON_PATH)
        except ValueError as exc:
            show_error(None, "Load error", str(exc))
            self._store_unreadable = True
            return ExpansionStore()
        self._store_unreadable = False
        return store

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
        self.stack.addWidget(self._build_settings_page())
        self.stack.addWidget(self._build_help_page())
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
        self.nav.setIconSize(QSize(_NAV_ICON_SIZE, _NAV_ICON_SIZE))
        for code_point, fallback, label in _NAV_ITEMS:
            QListWidgetItem(
                nav_icon(code_point, fallback, self.theme), label, self.nav
            )
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

    def _settings_group(self, layout: QVBoxLayout, title: str, blurb: str) -> None:
        heading = QLabel(title)
        heading.setObjectName("Heading")
        layout.addSpacing(6)
        layout.addWidget(heading)
        note = QLabel(blurb)
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)

        heading = QLabel("Settings")
        heading.setObjectName("Heading")
        layout.addWidget(heading)

        self._settings_group(
            layout,
            "Generated AutoHotkey script",
            "The full path of the script this app writes and runs, including "
            "the file name. Generate & Run AHK replaces this file.",
        )
        path_row = QHBoxLayout()
        self.ahk_path_edit = QLineEdit(self.settings.generated_ahk_path)
        self.ahk_path_edit.editingFinished.connect(self.save_settings)
        path_row.addWidget(self.ahk_path_edit, 1)
        ahk_browse = QPushButton("Browse...")
        ahk_browse.clicked.connect(self.browse_ahk_path)
        path_row.addWidget(ahk_browse)
        layout.addLayout(path_row)

        self._settings_group(
            layout, "Appearance", "The same toggle as the one in the sidebar."
        )
        theme_row = QHBoxLayout()
        self.settings_theme_button = QPushButton()
        self.settings_theme_button.clicked.connect(self.toggle_theme)
        theme_row.addWidget(self.settings_theme_button)
        theme_row.addStretch(1)
        layout.addLayout(theme_row)

        self._settings_group(
            layout,
            "Backup folder",
            f"Where copies of {JSON_PATH.name} and the generated script are "
            f"kept. The {BACKUP_RETENTION_LIMIT} most recent of each are "
            "retained. Leave blank to use the default folder beside the app. "
            "Changing this offers to move the existing backups across.",
        )
        backup_row = QHBoxLayout()
        self.backup_dir_edit = QLineEdit(self.settings.backup_directory)
        self.backup_dir_edit.setPlaceholderText(str(DEFAULT_BACKUP_DIR))
        self.backup_dir_edit.editingFinished.connect(self.apply_backup_dir_change)
        backup_row.addWidget(self.backup_dir_edit, 1)
        backup_browse = QPushButton("Browse...")
        backup_browse.clicked.connect(self.browse_backup_dir)
        backup_row.addWidget(backup_browse)
        backup_default = QPushButton("Use default")
        backup_default.clicked.connect(self.use_default_backup_dir)
        backup_row.addWidget(backup_default)
        layout.addLayout(backup_row)

        self._settings_group(
            layout,
            "Restore from backup",
            "Replaces the current file with the backup you pick. The file "
            "being replaced is backed up first, so this can be undone.",
        )
        restore_row = QHBoxLayout()
        json_button = QPushButton("Restore expansions backup...")
        json_button.clicked.connect(self.restore_json_backup)
        restore_row.addWidget(json_button)
        ahk_button = QPushButton("Restore AHK script backup...")
        ahk_button.clicked.connect(self.restore_ahk_backup)
        restore_row.addWidget(ahk_button)
        restore_row.addStretch(1)
        layout.addLayout(restore_row)

        layout.addStretch(1)
        return page

    def _build_help_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)

        heading = QLabel("Help")
        heading.setObjectName("Heading")
        layout.addWidget(heading)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setHtml(HELP_HTML)
        layout.addWidget(browser, 1)
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
        # Delete and Toggle On/Off act on the whole selection, so Ctrl and
        # Shift pick out more than one row here.
        self.tree.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        header = self.tree.horizontalHeader()
        self.tree.setColumnWidth(0, 44)
        self.tree.setColumnWidth(1, 130)
        self.tree.setColumnWidth(2, 300)
        header.setStretchLastSection(True)
        # Selecting a row no longer loads it: that would fight with building a
        # multi-row selection, and it overwrote the form on a stray click.
        self.tree.cellDoubleClicked.connect(
            lambda row, _column: self.load_double_clicked_expansion(row)
        )
        layout.addWidget(self.tree, 1)

        actions = QHBoxLayout()
        for text, slot in (
            ("New", self.new_expansion),
            ("Edit", self.load_selected_expansion),
            ("Delete", self.delete_expansion),
            ("Toggle On/Off", self.toggle_enabled),
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

        self.enabled_check = QCheckBox("On")
        self.enabled_check.setChecked(True)
        layout.addWidget(self.enabled_check)

        self.omit_end_char_check = QCheckBox("Drop the character that triggered it")
        self.omit_end_char_check.setToolTip(
            "Typing the trigger followed by a space normally leaves that space "
            "after the replacement. Tick this to end the expansion exactly "
            "where its replacement does."
        )
        layout.addWidget(self.omit_end_char_check)

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
        # Alphabetical order puts date_time first; an empty form should open on
        # the same type New gives you, not on the one that hides the most.
        self.variable_type_combo.setCurrentText("text_input")
        self.variable_prompt_edit = QLineEdit()
        self.variable_default_edit = QLineEdit()
        form_layout.addWidget(QLabel("Name"), 0, 0)
        form_layout.addWidget(self.variable_name_edit, 0, 1)
        form_layout.addWidget(QLabel("Type"), 1, 0)
        form_layout.addWidget(self.variable_type_combo, 1, 1)
        self.variable_prompt_label = QLabel("Prompt text")
        form_layout.addWidget(self.variable_prompt_label, 2, 0)
        form_layout.addWidget(self.variable_prompt_edit, 2, 1)
        # The field and the note that stands in for it share a row, in a box of
        # their own so hiding either one cannot leave the other overlapping it.
        self.variable_default_label = QLabel("Default")
        form_layout.addWidget(self.variable_default_label, 3, 0)
        self.variable_default_note = self._form_note()
        form_layout.addWidget(
            self._stacked_field(self.variable_default_edit, self.variable_default_note), 3, 1
        )
        self.variable_options_label = QLabel("List options")
        form_layout.addWidget(self.variable_options_label, 4, 0, Qt.AlignmentFlag.AlignTop)
        self.variable_options_text = QPlainTextEdit()
        self.variable_options_text.setMaximumHeight(120)
        self.variable_options_note = self._form_note()
        form_layout.addWidget(
            self._stacked_field(self.variable_options_text, self.variable_options_note), 4, 1
        )
        self.variable_type_combo.currentTextChanged.connect(
            lambda _type: self.sync_variable_form_to_type()
        )
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
        self.sync_variable_form_to_type()

        splitter.addWidget(left)
        splitter.addWidget(form)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([360, 620])
        outer.addWidget(splitter)
        return page

    @staticmethod
    def _form_note() -> QLabel:
        """A muted explanation that stands in for a field the type never uses."""
        note = QLabel()
        note.setObjectName("Muted")
        note.setWordWrap(True)
        note.setVisible(False)
        return note

    @staticmethod
    def _stacked_field(field: QWidget, note: QLabel) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(field)
        layout.addWidget(note)
        return box

    def sync_variable_form_to_type(self) -> None:
        """Show only the value fields the selected variable type reads.

        Each type ignores the others' fields entirely, so leaving them on
        screen invited values that nothing would ever read -- a format typed
        into a list_selection, options listed against a text_input.
        """
        kind = self.variable_type_combo.currentText().strip()
        is_date = kind == "date_time"
        is_list = kind == "list_selection"

        # date_time asks nothing: it formats the clock as the expansion fires.
        self.variable_prompt_label.setVisible(not is_date)
        self.variable_prompt_edit.setVisible(not is_date)

        self.variable_default_label.setText("Format" if is_date else "Default")
        self.variable_default_edit.setVisible(not is_list)
        self.variable_default_note.setText(LIST_SELECTION_DEFAULT_NOTE)
        self.variable_default_note.setVisible(is_list)

        self.variable_options_label.setVisible(is_list)
        self.variable_options_text.setVisible(is_list)
        self.variable_options_note.setText(
            DATE_FORMAT_NOTE if is_date else TEXT_INPUT_DEFAULT_NOTE
        )
        self.variable_options_note.setVisible(not is_list)

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

        # The script path moved to Settings: it is configured once, and it was
        # taking up a row under every page.
        action_row = QHBoxLayout()
        self.status_label = QLabel("Ready.")
        self.status_label.setObjectName("Muted")
        action_row.addWidget(self.status_label, 1)
        # Beside the status rather than in it: the status line is overwritten
        # by the next action, and an unsaved edit has to stay visible until it
        # is resolved. Blank whenever the window and the file agree.
        self.unsaved_label = QLabel("")
        self.unsaved_label.setObjectName("Warn")
        action_row.addWidget(self.unsaved_label)
        # No separate save button: every edit persists as it is applied, and
        # Generate & Run writes the store before generating regardless.
        for text, slot, primary in (
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
        # instance() is inherited from QCoreApplication and typed as returning
        # it, but the stylesheet lives on QApplication. isinstance both narrows
        # the type and covers the None case.
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setStyleSheet(build_stylesheet(self.theme))
        label = "☀  Light mode" if self.theme == "dark" else "☾  Dark mode"
        self.theme_button.setText(label)
        # The Settings page mirrors the sidebar toggle, so both must relabel.
        if hasattr(self, "settings_theme_button"):
            self.settings_theme_button.setText(label)
        self._refresh_nav_icons()
        self._apply_titlebar_theme()

    def _refresh_nav_icons(self) -> None:
        """Redraw the sidebar glyphs, whose colour is baked into the pixmap."""
        for row, (code_point, fallback, _label) in enumerate(_NAV_ITEMS):
            item = self.nav.item(row)
            if item is not None:
                item.setIcon(nav_icon(code_point, fallback, self.theme))

    def _apply_titlebar_theme(self) -> None:
        """Match the native Windows title bar to the current theme (Win 10/11).

        Publishes the theme for the dialogs as well, which are separate
        top-level windows and get it through TitleBarThemeFilter.
        """
        set_titlebar_theme(self.theme)
        apply_titlebar_theme(self, repaint=True)

    def toggle_theme(self) -> None:
        self.theme = "light" if self.theme == "dark" else "dark"
        save_theme_pref(self.theme)
        self.apply_theme()

    # -- table helpers -----------------------------------------------------
    @staticmethod
    def _table_selected_store_index(table: QTableWidget) -> int | None:
        indexes = ExpansionApp._table_selected_store_indexes(table)
        return indexes[0] if indexes else None

    @staticmethod
    def _table_store_index(table: QTableWidget, row: int) -> int | None:
        """The store index a table row stands for, or None if there is no row."""
        if row < 0:
            return None
        item = table.item(row, 0)
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    @staticmethod
    def _table_selected_store_indexes(table: QTableWidget) -> list[int]:
        """Every selected row's index into the store, in store order."""
        indexes: list[int] = []
        for row in table.selectionModel().selectedRows():
            stored = ExpansionApp._table_store_index(table, row.row())
            if stored is not None:
                indexes.append(stored)
        return sorted(indexes)

    @staticmethod
    def _table_rows_by_store_index(table: QTableWidget) -> dict[int, int]:
        rows: dict[int, int] = {}
        for row in range(table.rowCount()):
            stored = ExpansionApp._table_store_index(table, row)
            if stored is not None:
                rows[stored] = row
        return rows

    @staticmethod
    def _select_store_indexes(
        table: QTableWidget, indexes: list[int], focus: int | None = None
    ) -> None:
        """Reselect rows by store index, after a refresh rebuilt the table.

        setRangeSelected adds to the selection; selectRow would clear what the
        previous row just selected. The focus rectangle has to be put back by
        hand as well -- a rebuilt table has no current row, and Edit reads the
        current row to decide which of several selected rows to open.
        """
        rows = ExpansionApp._table_rows_by_store_index(table)
        table.clearSelection()
        last_column = table.columnCount() - 1
        for index in indexes:
            row = rows.get(index)
            if row is not None:
                table.setRangeSelected(
                    QTableWidgetSelectionRange(row, 0, row, last_column), True
                )
        wanted = focus if focus in rows else (indexes[0] if indexes else None)
        focus_row = rows.get(wanted) if wanted is not None else None
        if focus_row is not None:
            # NoUpdate: move the focus rectangle without touching the selection
            # that was just rebuilt.
            table.selectionModel().setCurrentIndex(
                table.model().index(focus_row, 0),
                QItemSelectionModel.SelectionFlag.NoUpdate,
            )

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

        # The sidebar drives the form's section only while the form is not
        # holding anything: adding, renaming or deleting a section rebuilds
        # this list, and an open editor's unapplied choice of section is an
        # answer to "where should this go", not a view of the list. Losing it
        # here moved the expansion to the sidebar's section on the next Apply,
        # with nothing on screen to say so.
        chosen = self.section_combo.currentText()
        keep = self.current_expansion is not None and chosen in self.store.sections
        self.section_combo.blockSignals(True)
        self.section_combo.clear()
        self.section_combo.addItems(self.store.sections)
        self.section_combo.setCurrentText(chosen if keep else selected)
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
        self.load_variable(self.store.variables[index])

    def load_variable(self, variable: VariableDef) -> None:
        """Fill the form from a stored variable, every box of it.

        Used after Apply as well as on selection: a type change hides the
        fields the new type ignores without emptying them, so a form left as
        typed still held the dropped value out of sight, ready to come back the
        moment the type was changed back.
        """
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
        kind = self.variable_type_combo.currentText().strip()
        # Only what this type reads is saved. A field the type ignores is
        # hidden, so carrying its old contents through would store something
        # the user can no longer see, to surface again on a later type change.
        options = [
            line.strip()
            for line in self.variable_options_text.toPlainText().splitlines()
            if line.strip()
        ]
        variable = VariableDef(
            name=self.variable_name_edit.text().strip(),
            type=kind,
            prompt_text="" if kind == "date_time" else self.variable_prompt_edit.text().strip(),
            default_value=(
                "" if kind == "list_selection" else self.variable_default_edit.text().strip()
            ),
            list_options=options if kind == "list_selection" else [],
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

    def _cascade_rename(
        self, kind: ReferenceKind, old: str, new: str, label: str
    ) -> bool:
        """Ask before repointing the library at a renamed item, then do it.

        A rename used to change the definition and nothing else, so every
        {VAR:old} still in the library was left undefined. That state autosaved
        without complaint and only surfaced at Generate & Run, by which point
        the rename that caused it was several actions ago.

        Returns False if the user declined, in which case the caller must
        abandon the rename entirely rather than apply half of it.
        """
        users = find_references(self.store, kind, old)
        if not users:
            return True
        if not confirm(
            self,
            f"Rename {label}",
            f'"{old}" is used by {item_count(len(users))}:\n\n'
            f"{reference_listing(users)}\n\n"
            f'Update them to use "{new}"?\n\n'
            "Choosing No leaves the rename unapplied, because renaming "
            "without updating them would stop the script generating.",
        ):
            return False
        rename_references(self.store, kind, old, new)
        # The editors hold their own copy of the text, which may include edits
        # not applied yet. Renaming in place keeps those rather than reloading
        # over them from the store.
        for box in (self.replacement_text, self.template_body_text):
            text = box.toPlainText()
            updated = rename_in_text(text, kind, old, new)
            if updated != text:
                box.setPlainText(updated)
        return True

    def apply_variable(self) -> None:
        try:
            variable = self.read_variable_form()
            self.ensure_unique_variable_name(variable.name, self.current_variable)
        except ValueError as exc:
            show_error(self, "Variable error", str(exc))
            return
        if self.current_variable is not None and self.current_variable.name != variable.name:
            if not self._cascade_rename(
                "VAR", self.current_variable.name, variable.name, "variable"
            ):
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
        self._persist_reporting(f'Saved variable "{variable.name}".')
        # Show what was actually saved. The fields this type ignores were
        # dropped on the way in, and the boxes still holding them are hidden,
        # so leaving the form as typed kept a dropped value alive off screen.
        self.load_variable(self.current_variable)

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
        if self.current_template is not None and self.current_template.name != template.name:
            if not self._cascade_rename(
                "TPL", self.current_template.name, template.name, "template"
            ):
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
        self._persist_reporting(f'Saved template "{template.name}".')

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

    def _blocked_by_references(
        self, kind: ReferenceKind, name: str, label: str
    ) -> bool:
        """Refuse to delete something the library still points at.

        Refused rather than confirmed: unlike a rename there is no repair to
        offer, so agreeing would knowingly leave a library that cannot
        generate. Naming the dependents makes the refusal actionable -- edit
        or delete those first, then the delete goes through.
        """
        users = find_references(self.store, kind, name)
        if not users:
            return False
        show_error(
            self,
            f"Delete {label}",
            f'"{name}" is still used by {item_count(len(users))}:\n\n'
            f"{reference_listing(users)}\n\n"
            f"Deleting it would stop the script generating. Change those to "
            f"stop using it first.",
        )
        return True

    def delete_variable(self) -> None:
        index = self._table_selected_store_index(self.variable_tree)
        if index is None:
            show_info(self, "Delete variable", "Select a variable first.")
            return
        variable = self.store.variables[index]
        if self._blocked_by_references("VAR", variable.name, "variable"):
            return
        if not confirm(self, "Delete variable", f'Delete variable "{variable.name}"?'):
            return
        self.store.variables.remove(variable)
        self.current_variable = None
        self.new_variable()
        self.refresh_variables()
        self._persist_reporting(f'Deleted variable "{variable.name}".')

    def delete_template(self) -> None:
        index = self._table_selected_store_index(self.template_tree)
        if index is None:
            show_info(self, "Delete template", "Select a template first.")
            return
        template = self.store.templates[index]
        if self._blocked_by_references("TPL", template.name, "template"):
            return
        if not confirm(self, "Delete template", f'Delete template "{template.name}"?'):
            return
        self.store.templates.remove(template)
        self.current_template = None
        self.new_template()
        self.refresh_templates()
        self._persist_reporting(f'Deleted template "{template.name}".')

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
        self._persist_reporting(f'Duplicated template as "{copy.name}".')

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

    def selected_expansion_index(self) -> int | None:
        """The one row Edit opens: the focused row, when it is selected.

        Ctrl-clicking down the list leaves the focus rectangle on the last row
        clicked, so always opening the topmost selected row opened something
        the user was not pointing at. Falls back to the topmost when nothing is
        focused -- a refresh rebuilds the rows and clears the current row --
        or when the focus sits on a row outside the selection.
        """
        indexes = self.selected_expansion_indexes()
        if not indexes:
            return None
        focused = self._table_store_index(self.tree, self.tree.currentRow())
        return focused if focused in indexes else indexes[0]

    def selected_expansion_indexes(self) -> list[int]:
        return self._table_selected_store_indexes(self.tree)

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
        self._persist_reporting(f'Added section "{name.strip()}".')

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
        self._persist_reporting(f'Renamed section to "{new_name.strip()}".')

    def delete_section(self) -> None:
        section = self.selected_section
        # Held on to because the store drops them, and the editor has to know
        # whether what it is holding was one of them. Clearing it either way
        # left it pointing at an expansion no longer in the library, which
        # Apply would then edit and persist into nothing.
        doomed = [expansion for expansion in self.store.expansions if expansion.section == section]
        if not confirm(
            self, "Delete section", f'Delete "{section}" and {len(doomed)} expansion(s)?'
        ):
            return
        self.store.delete_section(section)
        self.selected_section = self.store.sections[0]
        self.refresh_sections()
        self.refresh_expansions()
        self._forget_deleted_expansion(doomed)
        self._persist_reporting(f'Deleted section "{section}".')

    def new_expansion(self) -> None:
        self.current_expansion = None
        self.clear_form(keep_section=True)
        self.tree.clearSelection()

    def load_selected_expansion(self) -> None:
        index = self.selected_expansion_index()
        if index is None:
            # Selecting a row no longer fills the form, so Edit doing nothing
            # at all would look like the button was broken.
            show_info(self, "Edit expansion", "Select an expansion first.")
            return
        self.load_expansion(index)

    def load_double_clicked_expansion(self, row: int) -> None:
        """Open the row that was double-clicked, whatever else is selected.

        Pressing a row that is already part of a multi-row selection does not
        collapse that selection: Qt defers that to the mouse release so the
        selection can be dragged. The double click therefore arrives with
        several rows still selected, and reading the selection instead of the
        row under the pointer opened a different expansion from the one being
        double-clicked.
        """
        index = self._table_store_index(self.tree, row)
        if index is not None:
            self.load_expansion(index)

    def load_expansion(self, index: int) -> None:
        expansion = self.store.expansions[index]
        self.current_expansion = expansion
        self.section_combo.setCurrentText(expansion.section)
        self.trigger_edit.setText(expansion.trigger)
        self.enabled_check.setChecked(expansion.enabled)
        self.omit_end_char_check.setChecked(expansion.omit_end_char)
        self.replacement_text.setPlainText(expansion.replacement)
        self.notes_text.setPlainText(expansion.notes)

    # -- insertion actions -------------------------------------------------
    def insert_date_time(self, target: QPlainTextEdit | None = None) -> None:
        dialog = DateTimeDialog(self)
        if dialog.exec() and dialog.choice:
            self.insert_snippet(dialog.choice, target)

    def insert_input_box(self, target: QPlainTextEdit | None = None) -> None:
        dialog = InputPlaceholderDialog(self)
        if dialog.exec() and dialog.choice:
            self.insert_snippet(dialog.choice, target)

    def insert_list_selection(self, target: QPlainTextEdit | None = None) -> None:
        dialog = SelectPlaceholderDialog(self)
        if dialog.exec() and dialog.choice:
            self.insert_snippet(dialog.choice, target)

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
        if dialog.exec() and dialog.choice:
            self.insert_snippet(f"{{VAR:{dialog.choice}}}", target)

    def insert_template(self, target: QPlainTextEdit | None = None) -> None:
        names = [template.name for template in self.store.templates]
        if not names:
            show_info(self, "Insert Template", "Create a template first.")
            return
        dialog = LibrarySelectionDialog(self, "Insert Template", names)
        if dialog.exec() and dialog.choice:
            self.insert_snippet(f"{{TPL:{dialog.choice}}}", target)

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
            outcome = f'Added trigger "{expansion.trigger}".'
        else:
            self.current_expansion.section = expansion.section
            self.current_expansion.trigger = expansion.trigger
            self.current_expansion.replacement = expansion.replacement
            self.current_expansion.enabled = expansion.enabled
            self.current_expansion.notes = expansion.notes
            self.current_expansion.omit_end_char = expansion.omit_end_char
            outcome = f'Updated trigger "{expansion.trigger}".'

        self.selected_section = expansion.section
        self.refresh_sections()
        self.refresh_expansions()
        # Reported after the write rather than before it. Setting the status
        # first happened to survive a refusal only because persist overwrote
        # it, which is the wrong way round to depend on.
        self._persist_reporting(outcome)
        # The applied expansion now lives in the list, so the form goes back to
        # a blank new-expansion state rather than holding a stale copy of it.
        self.new_expansion()
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

        return Expansion(
            section,
            trigger,
            replacement,
            self.enabled_check.isChecked(),
            notes,
            self.omit_end_char_check.isChecked(),
        )

    def _forget_deleted_expansion(self, deleted: list[Expansion]) -> None:
        """Blank the editor only if what it holds is one of the deleted rows.

        Selecting a row no longer loads it, so the selection and the open
        expansion are now two different things: clearing regardless threw away
        unapplied edits to an expansion nobody asked to delete. Identity, not
        ==, because duplicate triggers are allowed and two records that compare
        equal are still different rows.
        """
        if not any(expansion is self.current_expansion for expansion in deleted):
            return
        self.current_expansion = None
        self.clear_form(keep_section=True)

    def delete_expansion(self) -> None:
        indexes = self.selected_expansion_indexes()
        if not indexes:
            show_info(self, "Delete expansion", "Select an expansion first.")
            return
        expansions = [self.store.expansions[index] for index in indexes]
        if len(expansions) == 1:
            title = "Delete expansion"
            question = f'Delete trigger "{expansions[0].trigger}"?'
            outcome = f'Deleted trigger "{expansions[0].trigger}".'
        else:
            title = "Delete expansions"
            question = f"Delete {len(expansions)} selected expansions?"
            outcome = f"Deleted {len(expansions)} expansions."
        if not confirm(self, title, question):
            return
        # Highest index first, so each removal leaves the lower ones in place.
        for index in reversed(indexes):
            del self.store.expansions[index]
        self._forget_deleted_expansion(expansions)
        self.refresh_expansions()
        self._persist_reporting(outcome)

    def toggle_enabled(self) -> None:
        indexes = self.selected_expansion_indexes()
        if not indexes:
            show_info(self, "Toggle On/Off", "Select an expansion first.")
            return
        expansions = [self.store.expansions[index] for index in indexes]
        focused = self._table_store_index(self.tree, self.tree.currentRow())
        # One state for the whole selection rather than flipping each row in
        # place, so a mixed selection comes out consistent instead of merely
        # inverted. A single row still just flips.
        enabled = not all(expansion.enabled for expansion in expansions)
        for expansion in expansions:
            expansion.enabled = enabled
        self.refresh_expansions()
        # The refresh rebuilt the rows, so put the selection and the focus back
        # for a second press.
        self._select_store_indexes(self.tree, indexes, focused)
        state = "Enabled" if enabled else "Disabled"
        if len(expansions) == 1:
            outcome = f'{state} "{expansions[0].trigger}".'
        else:
            outcome = f"{state} {len(expansions)} expansions."
        self._persist_reporting(outcome)

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

    def save_settings(self, announce: bool = True) -> None:
        """Write settings out as they change.

        Waiting for Generate & Run was survivable when the only setting was a
        path that is easy to retype. It is not survivable for the backup
        folder: a change that moved files but was never persisted would leave
        the setting pointing at one folder and the backups sitting in another,
        so the restore lists would come up empty.
        """
        self.settings.generated_ahk_path = str(self.current_ahk_path())
        self.ahk_path_edit.setText(self.settings.generated_ahk_path)
        self.settings.backup_directory = self.backup_dir_edit.text().strip()
        try:
            self.settings.save(SETTINGS_PATH)
        except OSError as exc:
            show_error(self, "Settings error", f"Could not save {SETTINGS_PATH.name}: {exc}")
            return
        if announce:
            self.set_status(f"Saved {SETTINGS_PATH.name}.")

    def _backup_once(self) -> None:
        """Copy the store file aside before this session's first write.

        Taken here rather than at launch because the file on disk is still the
        previous session's state until that first write -- so this captures the
        same thing a start-up backup would, while a session that only browses
        writes no backup at all. That matters: with a retention limit, backing
        up on every launch would rotate genuinely useful copies out.

        Autosave is what makes this necessary. Leaving without saving used to
        be an implicit undo, and it no longer is.
        """
        if self._session_backed_up:
            return
        # Set before attempting, so a failing backup is not retried on every
        # subsequent save.
        self._session_backed_up = True
        try:
            backup_file(JSON_PATH, self.current_backup_dir())
        except OSError as exc:
            show_warning(
                self,
                "Backup",
                f"Could not back up {JSON_PATH.name}: {exc}\n\n"
                "Your changes will still be saved.",
            )

    # -- backup location ---------------------------------------------------
    @staticmethod
    def _resolve_backup_dir(configured: str) -> Path:
        cleaned = configured.strip()
        return Path(cleaned).expanduser() if cleaned else DEFAULT_BACKUP_DIR

    def _backup_targets(self) -> list[Path]:
        """The files that have backups: the library and the generated script."""
        return [JSON_PATH, self.current_ahk_path()]

    def current_backup_dir(self) -> Path | None:
        """The folder backups belong in, or None to fall back beside the file.

        A default folder that cannot be created is an install detail the user
        never asked about, so it falls back quietly. A folder they chose
        themselves failing is worth saying out loud -- but only once, or every
        save would nag.
        """
        target = self._active_backup_dir
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            if self.settings.backup_directory.strip() and not self._backup_dir_warned:
                self._backup_dir_warned = True
                show_warning(
                    self,
                    "Backup folder",
                    f"Could not use {target}:\n{exc}\n\n"
                    "Backups will be written next to the files instead.",
                )
            return None
        return target

    def _report_config_migration(self) -> None:
        """Say what the move into the config folder did, once, at startup."""
        moved, failed = self._config_migration
        if failed:
            show_warning(
                self,
                "Config folder",
                f"Could not move {', '.join(failed)} into "
                f"{CONFIG_DIR.name}. They have been left where they were, and "
                f"the app is using {CONFIG_DIR}. Move them across by hand to "
                "keep what they held.",
            )
        elif moved:
            self.set_status(f"Moved {', '.join(moved)} into {CONFIG_DIR.name}.")

    def _migrate_legacy_backups(self) -> None:
        """Collect backups left beside the files by earlier versions."""
        target = self.current_backup_dir()
        if target is None:
            return
        moved = 0
        for path in self._backup_targets():
            try:
                moved += migrate_backups(path, path.parent, target)
            except OSError:
                # Tidying is not worth failing startup over.
                continue
        if moved:
            self.set_status(f"Moved {moved} existing backup(s) into {target}.")

    def browse_backup_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose backup folder", str(self._active_backup_dir)
        )
        if not chosen:
            return
        self.backup_dir_edit.setText(chosen)
        self.apply_backup_dir_change()

    def use_default_backup_dir(self) -> None:
        self.backup_dir_edit.setText("")
        self.apply_backup_dir_change()

    def apply_backup_dir_change(self) -> None:
        """Persist a new backup folder, offering to bring existing copies along."""
        new_dir = self._resolve_backup_dir(self.backup_dir_edit.text())
        if new_dir == self._active_backup_dir:
            return

        previous_dir = self._active_backup_dir
        self._active_backup_dir = new_dir
        self._backup_dir_warned = False
        self.save_settings(announce=False)

        movable = sum(
            len(list_backups(path, previous_dir)) for path in self._backup_targets()
        )
        if not movable:
            self.set_status(f"Backups will be written to {new_dir}.")
            return

        if confirm(
            self,
            "Move backups",
            f"Move the {movable} existing backup(s) from\n{previous_dir}\n"
            f"to\n{new_dir}?\n\n"
            "Choosing No leaves them where they are, and they will no longer "
            "appear in the restore lists.",
        ):
            try:
                moved = sum(
                    migrate_backups(path, previous_dir, new_dir)
                    for path in self._backup_targets()
                )
            except OSError as exc:
                show_error(self, "Move backups", f"Could not move backups: {exc}")
                return
            self.set_status(f"Moved {moved} backup(s) to {new_dir}.")
        else:
            self.set_status(
                f"Backups will be written to {new_dir}. "
                f"{movable} older backup(s) left in {previous_dir}."
            )

    # -- restore -----------------------------------------------------------
    def _restore_from_backup(self, target: Path, label: str) -> Path | None:
        """Ask which backup of target to restore, and restore it.

        Returns the restored-from path so the caller can reload whatever the
        file feeds, or None if nothing was restored.
        """
        backup_dir = self.current_backup_dir()
        backups = list_backups(target, backup_dir)
        if not backups:
            show_info(
                self,
                f"Restore {label}",
                f"There are no backups of {target.name} yet.",
            )
            return None

        choices = [f"{backup_timestamp(item)}    ({item.name})" for item in backups]
        choice, ok = QInputDialog.getItem(
            self,
            f"Restore {label}",
            f"Replace {target.name} with this backup:",
            choices,
            0,
            False,
        )
        if not ok:
            return None
        selected = backups[choices.index(choice)]

        if not confirm(
            self,
            f"Restore {label}",
            f"Replace {target.name} with the backup from "
            f"{backup_timestamp(selected)}?\n\n"
            f"The current {target.name} is backed up first, so this can be "
            "undone.",
        ):
            return None

        try:
            safety_copy = restore_backup(selected, target, backup_dir)
        except (OSError, ValueError) as exc:
            show_error(self, f"Restore {label}", str(exc))
            return None

        message = f"Restored {target.name} from {backup_timestamp(selected)}."
        if safety_copy:
            message += f" Previous file kept as {safety_copy.name}."
        self.set_status(message)
        show_info(self, f"Restore {label}", message)
        return selected

    def restore_json_backup(self) -> None:
        if self._restore_from_backup(JSON_PATH, "expansions") is None:
            return
        # The file underneath the UI changed, so everything on screen is stale.
        self.store = self._load_store()
        # Reloaded from disk, so the window and the file agree again -- whatever
        # refused write left the marker behind has been superseded.
        self._set_unsaved(False)
        self.current_expansion = None
        self.current_variable = None
        self.current_template = None
        self.selected_section = self.store.sections[0]
        self.refresh_sections()
        self.refresh_expansions()
        self.refresh_variables()
        self.refresh_templates()
        self.clear_form()

    def restore_ahk_backup(self) -> None:
        target = self.current_ahk_path()
        if self._restore_from_backup(target, "AHK script") is None:
            return
        # The restored script is only inert text until something runs it, and
        # Generate & Run would immediately overwrite it from the library.
        if confirm(
            self,
            "Restore AHK script",
            f"Run the restored {target.name} now?\n\n"
            "Note that Generate & Run AHK will overwrite it again from the "
            "expansions library.",
        ):
            self.run_ahk()

    def _may_replace_unreadable_store(self) -> bool:
        """Whether to write over a store file that failed to load.

        Autosave means the first edit after a failed load would silently
        replace a recoverable file with whatever is in the window -- usually
        the empty store that stood in for it. _backup_once does copy the file
        aside first, but only once per session and into a folder that rotates,
        so a few sessions of ordinary use can retire the last good copy.

        Answering yes clears the flag, so the question is asked once and
        saving is normal from then on. Answering no leaves the file untouched
        and the in-memory change unsaved -- which persist already reports --
        and leaves the flag set, so the next edit asks again. That is the
        intended shape: the alternative is to stop saving silently, which is
        the failure this whole path exists to avoid.
        """
        if not self._store_unreadable:
            return True
        count = len(self.store.expansions)
        # Naming the count is the point of the warning: the usual case is the
        # empty store that stood in for the file, and "an empty library" says
        # that far more plainly than "0 expansions".
        replacing = (
            "an empty library"
            if count == 0
            else f"the {count} expansion currently in the window"
            if count == 1
            else f"the {count} expansions currently in the window"
        )
        if not confirm(
            self,
            "Replace unreadable library",
            f"{JSON_PATH.name} could not be read when this window opened, so "
            "it was not loaded.\n\n"
            f"Saving now replaces that file with {replacing}. If the file "
            "holds a library worth keeping, restore a backup from the Help "
            "page instead.\n\n"
            "Replace it? The current file is backed up first.",
        ):
            return False
        # The flag is cleared only once the copy exists. _backup_once will not
        # do: it treats a failed copy as a warning and lets the write proceed,
        # which is right for a readable file whose contents are already known
        # good, and wrong for this one -- here the copy is the only remaining
        # route back to whatever the file held.
        if not self._back_up_before_replacing():
            return False
        self._store_unreadable = False
        return True

    def _back_up_before_replacing(self) -> bool:
        """Copy the unreadable file aside, or refuse to replace it.

        The dialog above says the file is backed up first, so a failure here
        has to stop the write rather than warn and carry on. The flag stays
        set, so the next edit asks again and can succeed once whatever stopped
        the copy is resolved.
        """
        try:
            backup_file(JSON_PATH, self.current_backup_dir())
        except OSError as exc:
            show_error(
                self,
                "Replace unreadable library",
                f"Could not back up {JSON_PATH.name}: {exc}\n\n"
                f"{JSON_PATH.name} has been left as it is. Replacing it "
                "without a copy is the one way to lose what it holds for "
                "good, so nothing was written.",
            )
            return False
        # A missing file returns None and is not a failure: there is nothing to
        # copy and nothing to lose.
        #
        # This copy is also this session's backup, so _backup_once has nothing
        # left to do and cannot take a second one.
        self._session_backed_up = True
        return True

    def _set_unsaved(self, unsaved: bool) -> None:
        """Record and show whether the window holds changes not on disk."""
        self._unsaved_changes = unsaved
        self.unsaved_label.setText("Unsaved changes" if unsaved else "")

    def closeEvent(self, event: QCloseEvent) -> None:
        """Do not let a refused save leave quietly.

        Autosave means closing is normally free, and it stays free: this asks
        only when a write was actually refused, which is the one case where
        the window holds something the file does not.
        """
        if self._unsaved_changes and not confirm(
            self,
            "Unsaved changes",
            f"Changes in this window were not written to {JSON_PATH.name}.\n\n"
            "Closing now discards them. Close anyway?",
        ):
            event.ignore()
            return
        super().closeEvent(event)

    def persist(self) -> bool:
        """Write the store to disk immediately after a change to it.

        Every edit saves as it is applied, so there is no separate save step to
        remember. Returns False having already reported the failure; the
        in-memory change stands either way, which is why a refusal has to leave
        the unsaved marker behind rather than passing quietly.
        """
        if not self._may_replace_unreadable_store():
            self.set_status(f"{JSON_PATH.name} left unchanged; nothing was saved.")
            self._set_unsaved(True)
            return False
        self._backup_once()
        try:
            self.store.save(JSON_PATH)
        except OSError as exc:
            show_error(self, "Save error", f"Could not save {JSON_PATH.name}: {exc}")
            self._set_unsaved(True)
            return False
        self._set_unsaved(False)
        return True

    def _persist_reporting(self, success: str) -> bool:
        """Save, and report success only where there was some.

        Handlers used to set their own status after persist regardless of the
        result, overwriting the warning persist had just set -- so a refused
        write ended with 'Saved variable "x".' on screen while the file was
        untouched and the edit existed only in memory.
        """
        if not self.persist():
            return False
        self.set_status(success)
        return True

    def generate_and_run_ahk(self) -> None:
        ahk_path = self.current_ahk_path()
        self.save_settings()
        # Through persist rather than store.save: this is the one write that
        # did not take a backup first, and it is reachable straight from a
        # failed load, so it was the shortest path from a recoverable file to
        # an empty one. A refusal here stops the run too -- generating the
        # script from a library the user declined to save would overwrite the
        # script as well.
        if not self.persist():
            return
        try:
            backup_path = generate_ahk(
                self.store,
                ahk_path,
                backup=True,
                backup_dir=self.current_backup_dir(),
                theme=self.theme,
                icon_source=ICON_PATH,
            )
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
            command_line = str(process.get("CommandLine") or "")
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

        conflicts = count_import_conflicts(self.store, imported)
        conflict_action = "skip"
        if conflicts:
            dialog = ImportConflictDialog(self, conflicts)
            if not dialog.exec():
                return
            conflict_action = dialog.choice
            if conflict_action is None:
                return

        # Merged into a copy first. Whether the result can generate depends on
        # both libraries and on the action just chosen, so it cannot be settled
        # any earlier: a reference the imported file leaves open may be one this
        # library supplies, and two templates that are each fine alone can close
        # a cycle once they are together. Leaving it to generate time was not an
        # answer either, because the merge writes straight into the live store
        # and autosaves.
        candidate = copy_store(self.store)
        result = merge_imported_store(candidate, imported, conflict_action)
        # Refused only for what the import breaks. A library already unable to
        # generate is the user's to fix, and blaming an import for it would bar
        # them from importing at all until they had.
        #
        # Compared record by record, not "does either store have a problem":
        # that reading switched the check off entirely once the library had a
        # single fault of its own, so any number of further broken records
        # could be imported on top of it.
        before = placeholder_problems(self.store)
        introduced = [
            message
            for key, message in placeholder_problems(candidate).items()
            if before.get(key) != message
        ]
        if introduced:
            show_error(
                self,
                "Import error",
                f"{Path(file_path).name} was not imported: {introduced[0]}\n\n"
                "Nothing in your library has changed.",
            )
            return

        self.store = candidate
        self.selected_section = imported.sections[0] if imported.sections else self.store.sections[0]
        self.current_expansion = None
        self.current_variable = None
        self.current_template = None
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
        if (
            result.definitions_overwritten
            or result.definitions_renamed
            or result.definitions_skipped
        ):
            status += (
                f" Existing definitions: {result.definitions_overwritten} "
                f"overwritten, {result.definitions_renamed} renamed, "
                f"{result.definitions_skipped} skipped."
            )
        self._persist_reporting(status)

    # -- misc --------------------------------------------------------------
    def clear_search(self) -> None:
        self.search_edit.clear()
        self.refresh_expansions()

    def clear_form(self, keep_section: bool = False) -> None:
        if not keep_section:
            self.section_combo.setCurrentText(self.selected_section)
        self.trigger_edit.clear()
        self.enabled_check.setChecked(True)
        self.omit_end_char_check.setChecked(False)
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
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(sys.argv)
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
    # Parented to the app so it outlives main's frame; a filter that is garbage
    # collected stops filtering.
    app.installEventFilter(TitleBarThemeFilter(app))
    window = ExpansionApp()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
