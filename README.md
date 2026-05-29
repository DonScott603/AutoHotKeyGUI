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

The app reads and writes `expansions.json` in the project folder.

## Generate the AutoHotkey script

Use the **Generate .ahk** button in the app. It writes:

```text
text_expansions.ahk
```

If that file already exists, the app creates a timestamped backup before overwriting it, for example:

```text
text_expansions.20260529_143000.bak.ahk
```

Run the generated `.ahk` file with AutoHotkey v2.

## Import an existing `.ahk` file

Use **Import .ahk** to parse basic section comments and single-line hotstrings like:

```ahk
; === Email ===
::brb::Be right back.
```

The importer is intentionally conservative. It handles simple one-line hotstrings and section comments, but not complex script logic.

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
