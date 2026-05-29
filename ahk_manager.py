import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_JSON = "expansions.json"
DEFAULT_AHK = "text_expansions.ahk"
DEFAULT_SETTINGS = "settings.json"


@dataclass
class AppSettings:
    generated_ahk_path: str

    @classmethod
    def load(cls, path: Path, default_ahk_path: Path) -> "AppSettings":
        if not path.exists():
            return cls(str(default_ahk_path))

        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not load {path.name}: {exc}") from exc

        configured_path = str(data.get("generated_ahk_path") or "").strip()
        return cls(configured_path or str(default_ahk_path))

    def save(self, path: Path) -> None:
        data = {"generated_ahk_path": self.generated_ahk_path}
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@dataclass
class Expansion:
    section: str
    trigger: str
    replacement: str
    enabled: bool = True
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any], fallback_section: str = "General") -> "Expansion":
        return cls(
            section=str(data.get("section") or fallback_section).strip() or fallback_section,
            trigger=str(data.get("trigger") or "").strip(),
            replacement=str(data.get("replacement") or ""),
            enabled=bool(data.get("enabled", True)),
            notes=str(data.get("notes") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "trigger": self.trigger,
            "replacement": self.replacement,
            "enabled": self.enabled,
            "notes": self.notes,
        }


@dataclass
class ExpansionStore:
    sections: list[str] = field(default_factory=lambda: ["General"])
    expansions: list[Expansion] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "ExpansionStore":
        if not path.exists():
            return cls()

        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not load {path.name}: {exc}") from exc

        sections = [str(item).strip() for item in data.get("sections", []) if str(item).strip()]
        expansions = [
            Expansion.from_dict(item)
            for item in data.get("expansions", [])
            if isinstance(item, dict)
        ]

        for expansion in expansions:
            if expansion.section not in sections:
                sections.append(expansion.section)

        return cls(sections or ["General"], expansions)

    def save(self, path: Path) -> None:
        data = {
            "sections": self.sections,
            "expansions": [expansion.to_dict() for expansion in self.expansions],
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def add_section(self, name: str) -> None:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Section name cannot be blank.")
        if clean_name in self.sections:
            raise ValueError(f'Section "{clean_name}" already exists.')
        self.sections.append(clean_name)

    def rename_section(self, old_name: str, new_name: str) -> None:
        clean_name = new_name.strip()
        if not clean_name:
            raise ValueError("Section name cannot be blank.")
        if clean_name != old_name and clean_name in self.sections:
            raise ValueError(f'Section "{clean_name}" already exists.')

        index = self.sections.index(old_name)
        self.sections[index] = clean_name
        for expansion in self.expansions:
            if expansion.section == old_name:
                expansion.section = clean_name

    def delete_section(self, name: str) -> None:
        if name not in self.sections:
            return
        self.sections.remove(name)
        self.expansions = [expansion for expansion in self.expansions if expansion.section != name]
        if not self.sections:
            self.sections.append("General")

    def duplicate_triggers(self) -> dict[str, list[Expansion]]:
        grouped: dict[str, list[Expansion]] = {}
        for expansion in self.expansions:
            if not expansion.trigger:
                continue
            grouped.setdefault(expansion.trigger.lower(), []).append(expansion)
        return {
            trigger: matches
            for trigger, matches in grouped.items()
            if len(matches) > 1
        }


@dataclass
class ImportMergeResult:
    added: int = 0
    overwritten: int = 0
    skipped: int = 0
    renamed: int = 0
    conflicts: int = 0

    @property
    def total_changed(self) -> int:
        return self.added + self.overwritten + self.renamed


@dataclass
class TemplatePlaceholder:
    kind: str
    value: str
    args: list[str]


@dataclass
class RenderedExpansion:
    lines: list[str]
    needs_select_helper: bool = False
    needs_image_helper: bool = False


SECTION_RE = re.compile(r"^\s*;\s*=+\s*(?P<section>.*?)\s*=+\s*$")
HOTSTRING_RE = re.compile(r"^\s*(?P<disabled>;\s*)?:(?P<options>[^:]*)?:(?P<trigger>[^:\s][^:]*)::(?P<replacement>.*)$")
PLACEHOLDER_RE = re.compile(r"\{(AHK_EXPR|AHK_INPUT|AHK_SELECT|AHK_KEY|AHK_IMAGE):([^{}]*)\}")
PLACEHOLDER_START_RE = re.compile(r"\{AHK_(?:EXPR|INPUT|SELECT|KEY|IMAGE):")
VARIABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SUPPORTED_KEYS = {"Tab"}


def import_ahk(path: Path) -> ExpansionStore:
    if not path.exists():
        raise ValueError(f"{path} does not exist.")

    sections: list[str] = []
    expansions: list[Expansion] = []
    current_section = "General"

    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise ValueError(f"Could not read {path.name}: {exc}") from exc

    for line in lines:
        section_match = SECTION_RE.match(line)
        if section_match:
            current_section = section_match.group("section").strip() or "General"
            if current_section not in sections:
                sections.append(current_section)
            continue

        hotstring_match = HOTSTRING_RE.match(line)
        if hotstring_match:
            if current_section not in sections:
                sections.append(current_section)
            expansions.append(
                Expansion(
                    section=current_section,
                    trigger=hotstring_match.group("trigger").strip(),
                    replacement=hotstring_match.group("replacement"),
                    enabled=not bool(hotstring_match.group("disabled")),
                )
            )

    if not sections:
        sections.append("General")
    return ExpansionStore(sections=sections, expansions=expansions)


def count_import_conflicts(target: ExpansionStore, imported: ExpansionStore) -> int:
    return sum(
        1
        for expansion in imported.expansions
        if _find_expansion(target, expansion.section, expansion.trigger) is not None
    )


def merge_imported_store(
    target: ExpansionStore,
    imported: ExpansionStore,
    conflict_action: str = "skip",
) -> ImportMergeResult:
    if conflict_action not in {"skip", "overwrite", "rename"}:
        raise ValueError("conflict_action must be skip, overwrite, or rename.")

    result = ImportMergeResult()
    for section in imported.sections:
        if section not in target.sections:
            target.sections.append(section)

    for imported_expansion in imported.expansions:
        existing = _find_expansion(target, imported_expansion.section, imported_expansion.trigger)
        expansion = Expansion.from_dict(imported_expansion.to_dict())
        if existing is None:
            target.expansions.append(expansion)
            result.added += 1
            continue

        result.conflicts += 1
        if conflict_action == "skip":
            result.skipped += 1
        elif conflict_action == "overwrite":
            existing.replacement = expansion.replacement
            existing.enabled = expansion.enabled
            existing.notes = expansion.notes
            result.overwritten += 1
        else:
            expansion.trigger = _renamed_trigger(target, expansion.section, expansion.trigger)
            target.expansions.append(expansion)
            result.renamed += 1

    return result


def generate_ahk(store: ExpansionStore, path: Path, backup: bool = True) -> Path | None:
    validate_store_placeholders(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        backup_path = _backup_path(path)
        shutil.copy2(path, backup_path)
    else:
        backup_path = None

    path.write_text(render_ahk(store), encoding="utf-8")
    return backup_path


def render_ahk(store: ExpansionStore) -> str:
    validate_store_placeholders(store)
    lines = [
        "#Requires AutoHotkey v2.0",
        "; Generated by AutoHotkey Text Expansion Manager.",
        "; Edit expansions.json through the GUI, then regenerate this file.",
        "",
    ]
    rendered_sections: list[tuple[str, list[RenderedExpansion]]] = []
    needs_select_helper = False
    needs_image_helper = False

    for section in store.sections:
        rendered_expansions = [
            render_expansion(expansion)
            for expansion in store.expansions
            if expansion.section == section
        ]
        rendered_sections.append((section, rendered_expansions))
        needs_select_helper = needs_select_helper or any(
            item.needs_select_helper for item in rendered_expansions
        )
        needs_image_helper = needs_image_helper or any(
            item.needs_image_helper for item in rendered_expansions
        )

    if needs_select_helper:
        lines.extend(_select_helper_lines())
        lines.append("")
    if needs_image_helper:
        lines.extend(_image_helper_lines())
        lines.append("")

    for section, rendered_expansions in rendered_sections:
        lines.append(f"; === {section} ===")
        if not rendered_expansions:
            lines.append("; No expansions in this section.")
        for rendered in rendered_expansions:
            lines.extend(rendered.lines)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_expansion(expansion: Expansion) -> RenderedExpansion:
    segments = parse_replacement_template(expansion.replacement)
    dynamic = any(isinstance(segment, TemplatePlaceholder) for segment in segments)
    if not dynamic:
        line = f"::{expansion.trigger}::{_single_line_replacement(expansion.replacement)}"
        lines = [line]
        if expansion.notes:
            lines.append(f"; Notes: {expansion.notes}")
        return RenderedExpansion(_maybe_disable_lines(lines, expansion.enabled))

    lines = [f"::{expansion.trigger}::", "{"]
    lines.append("    __tem_result := \"\"")
    needs_select_helper = False
    needs_image_helper = False

    def flush_result() -> None:
        lines.append("    if (__tem_result != \"\") {")
        lines.append("        SendText(__tem_result)")
        lines.append("        __tem_result := \"\"")
        lines.append("    }")

    for segment in segments:
        if isinstance(segment, str):
            if segment:
                lines.append(f"    __tem_result .= {_ahk_string(segment)}")
            continue

        if segment.kind == "AHK_EXPR":
            lines.append(f"    __tem_result .= {segment.value}")
        elif segment.kind == "AHK_INPUT":
            variable, prompt, title, default = segment.args
            lines.append(f"    __tem_input_{variable} := InputBox({_ahk_string(prompt)}, {_ahk_string(title)}, , {_ahk_string(default)})")
            lines.append(f"    if (__tem_input_{variable}.Result = \"Cancel\")")
            lines.append("        return")
            lines.append(f"    {variable} := __tem_input_{variable}.Value")
            lines.append(f"    __tem_result .= {variable}")
        elif segment.kind == "AHK_SELECT":
            variable, prompt, title, *options = segment.args
            option_list = ", ".join(_ahk_string(option) for option in options)
            lines.append(f"    __tem_select_{variable} := TEM_Select({_ahk_string(prompt)}, {_ahk_string(title)}, [{option_list}])")
            lines.append(f"    if (!__tem_select_{variable}.ok)")
            lines.append("        return")
            lines.append(f"    {variable} := __tem_select_{variable}.value")
            lines.append(f"    __tem_result .= {variable}")
            needs_select_helper = True
        elif segment.kind == "AHK_KEY":
            key_name = segment.value
            flush_result()
            lines.append(f"    SendEvent(\"{{{key_name}}}\")")
            lines.append("    Sleep(100)")
        elif segment.kind == "AHK_IMAGE":
            image_path = segment.value
            flush_result()
            lines.append(f"    if (!TEM_PasteImage({_ahk_string(image_path)}))")
            lines.append("        return")
            needs_image_helper = True

    flush_result()
    lines.append("}")
    if expansion.notes:
        lines.append(f"; Notes: {expansion.notes}")
    return RenderedExpansion(
        _maybe_disable_lines(lines, expansion.enabled),
        needs_select_helper,
        needs_image_helper,
    )


def validate_store_placeholders(store: ExpansionStore) -> None:
    for expansion in store.expansions:
        try:
            parse_replacement_template(expansion.replacement)
        except ValueError as exc:
            raise ValueError(f'Invalid placeholder in trigger "{expansion.trigger}": {exc}') from exc


def parse_replacement_template(text: str) -> list[str | TemplatePlaceholder]:
    segments: list[str | TemplatePlaceholder] = []
    position = 0
    matched_starts: set[int] = set()
    for match in PLACEHOLDER_RE.finditer(text):
        matched_starts.add(match.start())
        if match.start() > position:
            segments.append(text[position:match.start()])
        segments.append(_parse_placeholder(match.group(1), match.group(2)))
        position = match.end()

    if position < len(text):
        segments.append(text[position:])

    _validate_unmatched_placeholders(text, matched_starts)
    return segments


def _parse_placeholder(kind: str, body: str) -> TemplatePlaceholder:
    if kind == "AHK_EXPR":
        expression = body.strip()
        if not expression:
            raise ValueError("AHK_EXPR requires an expression.")
        return TemplatePlaceholder(kind, expression, [])

    if kind == "AHK_INPUT":
        parts = body.split("|")
        if len(parts) not in {3, 4}:
            raise ValueError("AHK_INPUT must use {AHK_INPUT:variable|prompt|title|default}.")
        if len(parts) == 3:
            parts.append("")
        variable, prompt, title, default = [part.strip() for part in parts]
        _validate_variable_name(variable, "AHK_INPUT")
        if not prompt:
            raise ValueError("AHK_INPUT prompt cannot be blank.")
        if not title:
            raise ValueError("AHK_INPUT title cannot be blank.")
        return TemplatePlaceholder(kind, body, [variable, prompt, title, default])

    if kind == "AHK_SELECT":
        parts = body.split("|")
        if len(parts) < 5:
            raise ValueError(
                "AHK_SELECT must use {AHK_SELECT:variable|prompt|title|Option A||Option B}."
            )
        variable, prompt, title = [part.strip() for part in parts[:3]]
        options_blob = "|".join(parts[3:])
        options = [option.strip() for option in options_blob.split("||") if option.strip()]
        _validate_variable_name(variable, "AHK_SELECT")
        if not prompt:
            raise ValueError("AHK_SELECT prompt cannot be blank.")
        if not title:
            raise ValueError("AHK_SELECT title cannot be blank.")
        if not options:
            raise ValueError("AHK_SELECT requires at least one option.")
        return TemplatePlaceholder(kind, body, [variable, prompt, title, *options])

    if kind == "AHK_KEY":
        key_name = body.strip()
        if not key_name:
            raise ValueError("AHK_KEY requires a key name.")
        if key_name not in SUPPORTED_KEYS:
            raise ValueError("AHK_KEY currently supports only Tab.")
        return TemplatePlaceholder(kind, key_name, [])

    if kind == "AHK_IMAGE":
        image_path = body.strip()
        if not image_path:
            raise ValueError("AHK_IMAGE requires an image file path.")
        if any(char in image_path for char in "{}"):
            raise ValueError("AHK_IMAGE path cannot contain braces.")
        return TemplatePlaceholder(kind, image_path, [])

    raise ValueError(f"Unsupported placeholder type {kind}.")


def _validate_unmatched_placeholders(text: str, matched_starts: set[int]) -> None:
    for match in PLACEHOLDER_START_RE.finditer(text):
        if match.start() in matched_starts:
            continue
        closing = text.find("}", match.start())
        if closing == -1:
            raise ValueError("Placeholder is missing a closing brace.")
        raise ValueError("Placeholder syntax is malformed or contains unsupported braces.")


def _validate_variable_name(value: str, placeholder_name: str) -> None:
    if not VARIABLE_RE.match(value):
        raise ValueError(f"{placeholder_name} variable must be a valid AutoHotkey identifier.")


def _maybe_disable_lines(lines: list[str], enabled: bool) -> list[str]:
    if enabled:
        return lines
    return [f"; {line}" if line else ";" for line in lines]


def _ahk_string(value: str) -> str:
    escaped = (
        value.replace("`", "``")
        .replace("\r\n", "`r`n")
        .replace("\n", "`n")
        .replace("\r", "`r")
        .replace('"', '`"')
    )
    return f'"{escaped}"'


def _select_helper_lines() -> list[str]:
    return [
        "TEM_Select(prompt, title, options) {",
        "    selectGui := Gui(\"+AlwaysOnTop\", title)",
        "    selectGui.AddText(\"w280\", prompt)",
        "    dropdown := selectGui.AddDropDownList(\"w280 Choose1\", options)",
        "    result := {ok: false, value: \"\"}",
        "    okButton := selectGui.AddButton(\"Default w80\", \"OK\")",
        "    cancelButton := selectGui.AddButton(\"x+8 w80\", \"Cancel\")",
        "    okButton.OnEvent(\"Click\", (*) => (result.ok := true, result.value := dropdown.Text, selectGui.Destroy()))",
        "    cancelButton.OnEvent(\"Click\", (*) => selectGui.Destroy())",
        "    selectGui.OnEvent(\"Close\", (*) => selectGui.Destroy())",
        "    selectGui.Show()",
        "    guiHwnd := selectGui.Hwnd",
        "    WinWaitClose(\"ahk_id \" guiHwnd)",
        "    return result",
        "}",
    ]


def _image_helper_lines() -> list[str]:
    return [
        "TEM_PasteImage(imagePath) {",
        "    if (!FileExist(imagePath)) {",
        "        MsgBox(\"Image file not found:``n\" imagePath, \"Image placeholder\")",
        "        return false",
        "    }",
        "    psPath := A_Temp \"\\tem_clip_image_\" A_TickCount \".ps1\"",
        "    psScript := \"param([string]$Path)\" \"`n\"",
        "        . \"Add-Type -AssemblyName System.Windows.Forms\" \"`n\"",
        "        . \"Add-Type -AssemblyName System.Drawing\" \"`n\"",
        "        . \"$img = [System.Drawing.Image]::FromFile($Path)\" \"`n\"",
        "        . \"[System.Windows.Forms.Clipboard]::SetImage($img)\" \"`n\"",
        "        . \"$img.Dispose()\" \"`n\"",
        "    FileAppend(psScript, psPath, \"UTF-8\")",
        "    exitCode := RunWait('powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File \"' psPath '\" \"' imagePath '\"', , \"Hide\")",
        "    FileDelete(psPath)",
        "    if (exitCode != 0) {",
        "        MsgBox(\"Could not place image on clipboard:``n\" imagePath, \"Image placeholder\")",
        "        return false",
        "    }",
        "    Send(\"^v\")",
        "    return true",
        "}",
    ]


def _single_line_replacement(text: str) -> str:
    return text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def _backup_path(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return path.with_name(f"{path.stem}.{timestamp}.bak{path.suffix}")


def _find_expansion(store: ExpansionStore, section: str, trigger: str) -> Expansion | None:
    for expansion in store.expansions:
        if expansion.section == section and expansion.trigger.lower() == trigger.lower():
            return expansion
    return None


def _renamed_trigger(store: ExpansionStore, section: str, trigger: str) -> str:
    base = f"{trigger}_imported"
    candidate = base
    suffix = 2
    while _find_expansion(store, section, candidate) is not None:
        candidate = f"{base}{suffix}"
        suffix += 1
    return candidate
