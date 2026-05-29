# AutoHotkey Text Expansion Manager

A small Windows desktop app for managing AutoHotkey text expansion hotstrings without editing one large `.ahk` file by hand.

## Requirements

- Python 3
- Tkinter, which is included with the standard Windows Python installer
- AutoHotkey v2 to run the generated script

No third-party Python packages are required.

## Run the app

```powershell
python app.py
```

The app reads and writes `expansions.json` in the project folder. It also stores app preferences in `settings.json`.

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

Run the generated `.ahk` file with AutoHotkey v2, or use the app controls:

- **Run AHK** launches the configured generated script.
- **Reload AHK** stops and relaunches the script only if this app started that process during the current GUI session.

The app does not kill all AutoHotkey processes globally. If the script was started outside this app, reload will show a message explaining that only app-started processes can be cleanly reloaded.

## Template insertion helpers

The replacement editor includes helper buttons for inserting structured placeholders. These placeholders are stored as readable text in `expansions.json`; raw generated AutoHotkey code is not stored in your data file.

Simple literal expansions still generate one-line hotstrings:

```text
Thank you for your business.
```

Dynamic placeholders generate multi-line AutoHotkey v2 hotstrings at export time.

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

Sections are rendered as comments in the generated `.ahk` file:

```ahk
; === Email ===
::sig::Best regards, Your Name
```

Disabled expansions are commented out in the generated script.
