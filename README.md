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

The app reads and writes `expansions.json` in the project folder. It also stores the generated-script path in `settings.json` and the light/dark theme choice in `ui_prefs.json`.

The window uses a left sidebar to switch between the **Expansions**, **Variables**, and **Templates** views, and a theme toggle at the bottom of the sidebar switches between light and dark mode (defaulting to your OS setting).

## Generate the AutoHotkey script

Use the **Generated AHK path** field to choose where the generated script should be written. If no setting exists yet, it defaults to:

```text
text_expansions.ahk
```

Use **Browse** to choose a different location. The path is persisted in `settings.json`.

Use the **Generate .ahk** button to write the configured file.

If that file already exists, the app creates a timestamped backup before overwriting it, for example:

```text
text_expansions.20260529_143000.bak.ahk
```

Only the five most recent generated `.ahk` backups for that configured output file are retained. Older app-created backups are deleted automatically; unrelated files are left alone.

Run the generated `.ahk` file with AutoHotkey v2, or use the app controls:

- **Run AHK** launches the configured generated script.
- **Reload AHK** stops and relaunches only running AutoHotkey processes whose command line references the configured generated script path.

The app does not kill all AutoHotkey processes globally. Reload targets only the configured generated `.ahk` file, so unrelated AutoHotkey scripts are left alone. If process inspection is unavailable, the app warns and launches the configured script without stopping anything.

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
