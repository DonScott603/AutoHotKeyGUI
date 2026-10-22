# AutoHotkey Text Expansion Manager

A PySide6 desktop app that keeps a library of text expansions (`expansions.json`) and generates an AutoHotkey v2 script from it. Windows only; CI and the shipped exe use Python 3.11.

## Layout
- `ahk_manager.py` -- no Qt. The data model (`Expansion`, `VariableDef`, `TemplateDef`, `ExpansionStore`), JSON load/save, placeholder parsing, script generation (`render_ahk`, `render_expansion`) and `.ahk` import (`import_ahk`, `merge_imported_store`).
- `app.py` -- the PySide6 GUI (`ExpansionApp`). Keep logic that does not need Qt in `ahk_manager.py`, where it can be tested without a window.
- `tests/` -- one file per feature or bug.
- `AutoHotkeyExpansionManager.spec` -- the PyInstaller build. CI builds the exe on every PR and attaches it to releases.

## Commands
- Run the app: `python app.py`
- Tests, exactly as CI runs them: `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests`
- `python -m pytest` also works locally (pytest is installed, but not pinned in the requirements).
- Build: `python -m PyInstaller AutoHotkeyExpansionManager.spec`

## Tests
- Write tests as `unittest.TestCase` classes. CI uses `unittest discover`, which skips plain pytest-style functions silently.
- `tests/test_ahk_syntax.py` passes generated scripts to the real AutoHotkey in `/validate` mode. It skips when AutoHotkey v2 is not installed, but it is installed on this machine. When generation changes, add a case there: text assertions alone have let through scripts that AutoHotkey refused to load.
- GUI tests:
  - Point `app_module.JSON_PATH`, `SETTINGS_PATH`, `UI_PREFS_PATH`, `AHK_PATH` and `DEFAULT_BACKUP_DIR` at a temp directory, so they never touch the real library in the repo root.
  - Call `destroy_all_windows()` from `tests/qt_cleanup.py` in `setUp`.
  - Patch `app_module.show_warning` / `show_error` around any action that can raise a dialog. A modal dialog hangs a headless run instead of failing it. `tests/test_end_char.py` is a good model.

## Invariants
- **Generate → import must round-trip.** The generated script carries `; @tem:`, `; @tem-var:`, `; @tem-template:` and `; @tem-skipped:` marker comments, and `import_ahk` rebuilds the library from them. A new `Expansion` field needs adding in four places:
  - `from_dict` / `to_dict`
  - `_FIELD_TYPES` (used to validate the JSON load and the marker records)
  - `_source_marker`, when the generated line itself can't record the field
  - a round-trip test
- **The generated script must always load.** AutoHotkey refuses the whole file over a single bad line, so one broken expansion breaks every expansion. An expansion that can't generate is written as a `_skipped` record; anything that should stop generation goes in `placeholder_problems`.
- **Escaping:** text that lands in AutoHotkey string literals goes through `_ahk_string`, and static hotstring text goes through `_single_line_replacement`. A semicolon after whitespace opens a comment even inside a string.
- **Writes are atomic** (`_atomic_write_text`). Loading is strict about types and refuses rather than coerces, because autosave would write the coerced value back over the original.
- **Old libraries keep loading.** A new field defaults to the old behaviour, so existing `expansions.json` files open unchanged.

## Style
- Comments and docstrings explain *why*: the failure a line prevents, or what was measured. Match that density in surrounding code.
- Commit messages: an imperative subject line, then prose paragraphs on the problem and the reasoning.
- The user-facing help text is `HELP_HTML` in `app.py`; also update `README.md` when behaviour changes.
