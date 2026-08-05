# AutoHotkey Text Expansion Manager

A small Windows desktop app for managing AutoHotkey text expansion hotstrings without editing one large `.ahk` file by hand.

## Requirements

- Python 3.11+
- [PySide6](https://pypi.org/project/PySide6/) (Qt for Python) for the GUI
- AutoHotkey v2 to run the generated script

Install the Python dependencies with:

```powershell
python -m pip install -r requirements.txt
```

## Run the app

```powershell
python app.py
```

The app reads and writes `config\expansions.json`, creating the `config` folder on first save. This file holds your personal expansions and is not tracked in git. To start from a sample, copy the bundled example:

```powershell
New-Item -ItemType Directory -Force config
Copy-Item expansions.json.example config\expansions.json
```

If `expansions.json` is absent, the app simply starts with an empty store. It also stores the generated-script path in `config\settings.json` and the light/dark theme choice in `config\ui_prefs.json` (both untracked). An install from before the `config` folder existed has these three files moved into it once, on first run.

The window uses a left sidebar to switch between the **Expansions**, **Variables**, and **Templates** views, and a theme toggle at the bottom of the sidebar switches between light and dark mode (defaulting to your OS setting).

## Build a standalone executable

To distribute the app without requiring Python, build a single `.exe` with
[PyInstaller](https://pyinstaller.org/):

```powershell
python -m pip install -r requirements-dev.txt
python -m PyInstaller AutoHotkeyExpansionManager.spec
```

The executable is written to `dist\AutoHotkeyExpansionManager.exe`. It is
portable: it reads and writes `expansions.json`, `settings.json`, and
`ui_prefs.json` in a **`config` folder beside the `.exe`**, so run it from a
user-writable location (e.g. its own folder, not `C:\Program Files`). A working
install ends up as:

```
AutoHotkeyExpansionManager.exe
text_expansions.ahk          generated; this is the file AutoHotkey runs
config\
    expansions.json          your library
    settings.json            generated-script path, backup folder
    ui_prefs.json            light/dark choice
    TextExpansionManager.ico copied here for the script's tray and prompts
backups\                     copies of the library and the generated script
```

The generated script looks for its icon in `config\` first and then next to
itself, both relative to the script, so a `.ahk` copied to a machine with no app
installed still runs -- with the icon if one travelled with it, and with
AutoHotkey's default icon if not.

The bundled `.spec` produces a one-file, windowed (no console) build with the
app icon. Rebuild from scratch with:

```powershell
python -m PyInstaller --onefile --windowed --name AutoHotkeyExpansionManager `
  --icon app.ico --add-data "app.ico;." app.py
```

Releases are also built automatically: publishing a GitHub Release runs
`.github/workflows/build.yml`, which builds the exe and attaches it to the
release. The icon (`app.ico`) can be regenerated with `python tools/make_icon.py`.

## Generate the AutoHotkey script

Use the **Generated AHK path** field to choose where the generated script should be written. If no setting exists yet, it defaults to:

```text
text_expansions.ahk
```

Use **Browse** to choose a different location. The path is persisted in `settings.json`.

Use the **Generate & Run AHK** button to write the configured file and launch it (details below).

If that file already exists, the app creates a timestamped backup before overwriting it, for example:

```text
text_expansions.20260529_143000.bak.ahk
```

Only the five most recent generated `.ahk` backups for that configured output file are retained. Older app-created backups are deleted automatically; unrelated files are left alone.

Run the generated `.ahk` file with AutoHotkey v2, or use the app controls:

- **Generate & Run AHK** writes the configured file (creating a backup first, as above), stops any already-running instance of that script, then launches it. This is the usual way to apply your changes.
- **Run AHK** launches the configured generated script without regenerating it.

The app does not kill all AutoHotkey processes globally. Generate & Run AHK only stops AutoHotkey processes whose command line references the configured generated `.ahk` file, so unrelated AutoHotkey scripts are left alone. If process inspection is unavailable, the app warns and launches the configured script without stopping anything.

## Template insertion helpers

The replacement editor includes helper buttons for inserting structured placeholders. These placeholders are stored as readable text in `expansions.json`; raw generated AutoHotkey code is not stored in your data file.

Simple literal expansions still generate one-line hotstrings:

```text
Thank you for your business.
```

Dynamic placeholders generate multi-line AutoHotkey v2 hotstrings at export time.

## Variable Library

The **Variables** tab stores reusable named placeholders that can be inserted into any expansion or template. Variables are saved in `expansions.json` as readable definitions, not generated AutoHotkey code.

Supported variable types:

- `text_input`: asks for free-form text at expansion time.
- `list_selection`: asks the user to choose from saved options.
- `date_time`: inserts a `FormatTime` value using the saved format.

Examples:

- `client_name`: `text_input`
- `advisor_name`: `text_input`
- `status`: `list_selection`
- `today_iso`: `date_time` with default/format `yyyy-MM-dd`

Use **Insert Variable** in the expansion editor to insert:

```text
{VAR:client_name}
```

At generation time, `{VAR:client_name}` is resolved by the generator into the same runtime behavior as the lower-level placeholders such as `AHK_INPUT`, `AHK_SELECT`, or `AHK_EXPR`.

Variable names are case-sensitive and may contain only letters, numbers, and underscores. Undefined variable references stop generation with a clear validation error.

## Template Library

The **Templates** tab stores reusable expansion bodies. Templates are also saved in `expansions.json` as readable text.

Templates have:

- name
- optional description
- body text
- optional notes

Use **Insert Template** in the expansion or template editor to insert a readable template reference:

```text
{TPL:Client Follow-Up}
```

At generation time, the generator resolves the referenced template body.

Example template:

```text
Dear {VAR:client_name},

Thank you for taking the time to speak with me today.

Best,
{VAR:advisor_name}
```

Templates can include variables, other supported placeholders, and other templates:

```text
{TPL:Greeting}

Status: {VAR:status}
```

Circular template references are not allowed. For example, Template A cannot include Template B if Template B includes Template A, and a template cannot include itself. Generation fails with a clear validation error if a circular reference is detected. Duplicate template names are not allowed.

### Date/Time

Use **Insert Date/Time** to insert a FormatTime expression placeholder, for example:

```text
Today is {AHK_EXPR:FormatTime(A_Now, "yyyy-MM-dd")}
```

Common formats in the dialog:

- Short date: `MM/dd/yyyy`
- ISO date: `yyyy-MM-dd`
- Long date: `dddd, MMMM d, yyyy`
- Time: `h:mm tt`
- Date + time: `yyyy-MM-dd h:mm tt`
- Custom format

Supported date/time tokens are AutoHotkey v2 `FormatTime` tokens, including `yyyy`, `MM`, `dd`, `dddd`, `MMMM`, `h`, `mm`, and `tt`.

### Input Box

Use **Insert Input Box** when the expansion should ask for a value before inserting text:

```text
Dear {AHK_INPUT:client_name|Enter client name|Client Name|},
```

The fields are:

- variable name
- prompt text
- window title
- default value, optional

### List Selection

Use **Insert List Selection** when the expansion should ask the user to choose from a fixed list:

```text
Status: {AHK_SELECT:status|Choose status|Status|Pending||Approved||Rejected}
```

The fields are:

- variable name
- prompt text
- window title
- list options, one per line

The generated AutoHotkey script includes a small selection GUI only when a list selection placeholder is used.

### Tab

Use **Insert Tab** to insert a Tab keystroke at that point in the expansion:

```text
First column{AHK_KEY:Tab}Second column
```

For now, `AHK_KEY` supports only:

```text
{AHK_KEY:Tab}
```

Any expansion containing `AHK_KEY` is generated as a dynamic multi-line hotstring.

### Image

Use **Insert Image** to choose an image file and insert an image placeholder:

```text
{AHK_IMAGE:C:\Users\Scott\Pictures\logo.png}
```

Supported file types in the chooser:

- `.png`
- `.jpg`
- `.jpeg`
- `.gif`
- `.bmp`
- `.webp`

Image files are stored as file paths only. The image binary is not embedded in `expansions.json`. Moving, renaming, or deleting the image file will break that placeholder until the path is updated.

Image insertion is implemented as a clipboard paste helper in the generated AutoHotkey v2 script. It checks that the file exists, uses PowerShell/.NET to place the image on the Windows clipboard, then sends `Ctrl+V`. This works best in rich-text targets that accept pasted images, such as Word, Outlook, Teams, or browser editors. Plain text editors such as Notepad cannot accept pasted images.

## Previews

Use preview buttons to inspect app data without writing files:

- **Preview Expansion** shows section, trigger, enabled status, raw replacement text, resolved replacement text, placeholder summary, and the exact AutoHotkey v2 code for that expansion.
- **Preview Variable** shows the saved variable definition, example `{VAR:name}` placeholder, resolved lower-level placeholder form, and whether it requires dynamic runtime generation.
- **Preview Template** shows raw template body, resolved template body, placeholder summary, and nested templates expanded into readable placeholder form.

Preview terms:

- Raw text is exactly what is stored in app data.
- Resolved text expands `{TPL:...}` and `{VAR:...}` into readable placeholder form, but not raw generated AutoHotkey code.
- Generated AHK is the AutoHotkey v2 code that export would emit.

Previews are read-only and do not write `expansions.json` or `.ahk` files. If a preview reports undefined variables, undefined templates, malformed placeholders, or circular template references, fix those errors before generating or reloading AHK.

## Import an existing `.ahk` file

Use **Import .ahk** to parse basic section comments and single-line hotstrings like:

```ahk
; === Email ===
::brb::Be right back.
```

Import merges parsed sections and expansions into the current data. Existing sections are reused, and imported expansions are appended. If a trigger already exists in the target section, the app asks whether to skip duplicates, overwrite existing expansions, or keep both by renaming imported triggers.

The importer is intentionally conservative. It handles simple one-line hotstrings and section comments, but not complex script logic.

Dynamic generated hotstrings are intentionally not re-imported into placeholders. Edit dynamic expansions from the app or `expansions.json`, then regenerate the `.ahk` file.

## Data format

Each expansion has:

- `section`
- `trigger`
- `replacement`
- `enabled`
- `notes`

Generated hotstrings are case-sensitive by default. Triggers such as `Hsa` and `hsa` are treated as distinct triggers and can have different replacements.

Sections are rendered as comments in the generated `.ahk` file:

```ahk
; === Email ===
::sig::Best regards, Your Name
```

Disabled expansions are commented out in the generated script.
