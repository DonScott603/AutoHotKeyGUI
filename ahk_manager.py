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
BACKUP_RETENTION_LIMIT = 5


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
class VariableDef:
    name: str
    type: str
    prompt_text: str = ""
    default_value: str = ""
    list_options: list[str] = field(default_factory=list)
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VariableDef":
        options = data.get("list_options", [])
        if isinstance(options, str):
            options = [line.strip() for line in options.splitlines() if line.strip()]
        if not isinstance(options, list):
            options = []
        return cls(
            name=str(data.get("name") or "").strip(),
            type=str(data.get("type") or "text_input").strip(),
            prompt_text=str(data.get("prompt_text") or ""),
            default_value=str(data.get("default_value") or ""),
            list_options=[str(option).strip() for option in options if str(option).strip()],
            notes=str(data.get("notes") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "prompt_text": self.prompt_text,
            "default_value": self.default_value,
            "list_options": self.list_options,
            "notes": self.notes,
        }


@dataclass
class TemplateDef:
    name: str
    description: str = ""
    body: str = ""
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TemplateDef":
        return cls(
            name=str(data.get("name") or "").strip(),
            description=str(data.get("description") or ""),
            body=str(data.get("body") or ""),
            notes=str(data.get("notes") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "body": self.body,
            "notes": self.notes,
        }


@dataclass
class ExpansionStore:
    sections: list[str] = field(default_factory=lambda: ["General"])
    expansions: list[Expansion] = field(default_factory=list)
    variables: list[VariableDef] = field(default_factory=list)
    templates: list[TemplateDef] = field(default_factory=list)

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
        variables = [
            VariableDef.from_dict(item)
            for item in data.get("variables", [])
            if isinstance(item, dict)
        ]
        templates = [
            TemplateDef.from_dict(item)
            for item in data.get("templates", [])
            if isinstance(item, dict)
        ]

        for expansion in expansions:
            if expansion.section not in sections:
                sections.append(expansion.section)

        return cls(sections or ["General"], expansions, variables, templates)

    def save(self, path: Path) -> None:
        data = {
            "sections": self.sections,
            "expansions": [expansion.to_dict() for expansion in self.expansions],
            "variables": [variable.to_dict() for variable in self.variables],
            "templates": [template.to_dict() for template in self.templates],
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
            grouped.setdefault(expansion.trigger, []).append(expansion)
        return {
            trigger: matches
            for trigger, matches in grouped.items()
            if len(matches) > 1
        }

    def variable_by_name(self, name: str) -> VariableDef | None:
        for variable in self.variables:
            if variable.name == name:
                return variable
        return None

    def template_by_name(self, name: str) -> TemplateDef | None:
        for template in self.templates:
            if template.name == name:
                return template
        return None

    def duplicate_variable_names(self) -> dict[str, list[VariableDef]]:
        grouped: dict[str, list[VariableDef]] = {}
        for variable in self.variables:
            if variable.name:
                grouped.setdefault(variable.name, []).append(variable)
        return {name: matches for name, matches in grouped.items() if len(matches) > 1}

    def duplicate_template_names(self) -> dict[str, list[TemplateDef]]:
        grouped: dict[str, list[TemplateDef]] = {}
        for template in self.templates:
            if template.name:
                grouped.setdefault(template.name, []).append(template)
        return {name: matches for name, matches in grouped.items() if len(matches) > 1}


@dataclass
class ImportMergeResult:
    added: int = 0
    overwritten: int = 0
    skipped: int = 0
    renamed: int = 0
    conflicts: int = 0
    variables_added: int = 0
    templates_added: int = 0

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
    needs_paste_helper: bool = False
    needs_form_helper: bool = False


@dataclass
class PreviewResult:
    title: str
    content: str


SECTION_RE = re.compile(r"^\s*;\s*=+\s*(?P<section>.*?)\s*=+\s*$")
HOTSTRING_RE = re.compile(r"^\s*(?P<disabled>;\s*)?:(?P<options>[^:]*)?:(?P<trigger>[^:\s][^:]*)::(?P<replacement>.*)$")
# Machine-readable marker written before each generated hotstring. It carries the
# original replacement template (and notes) as JSON so dynamic expansions —
# whose AutoHotkey code block cannot be reversed into template syntax — survive a
# re-import (generate -> import -> generate) round trip.
SOURCE_MARKER_RE = re.compile(r"^\s*;\s*@tem:\s*(?P<json>.*)$")
# Variable and template definitions are inlined into each expansion's generated
# code, so the raw AHK cannot be reversed into the library. These header markers
# carry the definitions verbatim so they survive a generate -> import round trip.
VAR_MARKER_RE = re.compile(r"^\s*;\s*@tem-var:\s*(?P<json>.*)$")
TEMPLATE_MARKER_RE = re.compile(r"^\s*;\s*@tem-template:\s*(?P<json>.*)$")
PLACEHOLDER_RE = re.compile(r"\{(AHK_EXPR|AHK_INPUT|AHK_SELECT|AHK_KEY|AHK_IMAGE|VAR|TPL):([^{}]*)\}")
PLACEHOLDER_START_RE = re.compile(r"\{(?:AHK_(?:EXPR|INPUT|SELECT|KEY|IMAGE)|VAR|TPL):")
VARIABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SUPPORTED_KEYS = {"Tab"}
HOTSTRING_OPTIONS = "C"
# Static auto-replace hotstrings add "T" (Text mode) so the replacement is sent
# literally. Without it AutoHotkey interprets ^ + ! # { } as Send modifiers/keys
# (e.g. a leading/trailing "!" becomes Alt), corrupting the expansion.
STATIC_HOTSTRING_OPTIONS = "CT"
VARIABLE_TYPES = {"text_input", "list_selection", "date_time"}


def import_ahk(path: Path) -> ExpansionStore:
    if not path.exists():
        raise ValueError(f"{path} does not exist.")

    sections: list[str] = []
    expansions: list[Expansion] = []
    variables: list[VariableDef] = []
    templates: list[TemplateDef] = []
    current_section = "General"

    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise ValueError(f"Could not read {path.name}: {exc}") from exc

    pending_source: dict[str, Any] | None = None

    for index, line in enumerate(lines):
        var_match = VAR_MARKER_RE.match(line)
        if var_match:
            try:
                data = json.loads(var_match.group("json"))
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                variables.append(VariableDef.from_dict(data))
            continue

        template_match = TEMPLATE_MARKER_RE.match(line)
        if template_match:
            try:
                data = json.loads(template_match.group("json"))
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                templates.append(TemplateDef.from_dict(data))
            continue

        marker_match = SOURCE_MARKER_RE.match(line)
        if marker_match:
            try:
                data = json.loads(marker_match.group("json"))
            except json.JSONDecodeError:
                data = None
            pending_source = data if isinstance(data, dict) else None
            continue

        section_match = SECTION_RE.match(line)
        if section_match:
            current_section = section_match.group("section").strip() or "General"
            if current_section not in sections:
                sections.append(current_section)
            pending_source = None
            continue

        hotstring_match = HOTSTRING_RE.match(line)
        if hotstring_match:
            if current_section not in sections:
                sections.append(current_section)
            trigger = hotstring_match.group("trigger").strip()
            enabled = not bool(hotstring_match.group("disabled"))
            if pending_source is not None:
                # Generated file: the marker holds the authoritative template, so
                # dynamic (variable/input/date) expansions round-trip correctly.
                expansions.append(
                    Expansion(
                        section=current_section,
                        trigger=trigger,
                        replacement=str(pending_source.get("replacement", "")),
                        enabled=enabled,
                        notes=str(pending_source.get("notes", "")),
                    )
                )
            elif hotstring_match.group("replacement") == "" and (
                (block_open := _find_block_open(lines, index)) is not None
            ):
                # A dynamic hotstring from an unmarked (pre-marker) generated file.
                # Reconstruct the template from the generated code block; if the
                # block is not in a recognised form, skip it rather than import a
                # corrupt empty expansion.
                reconstructed = _reconstruct_replacement(lines, block_open)
                if reconstructed is not None:
                    expansions.append(
                        Expansion(
                            section=current_section,
                            trigger=trigger,
                            replacement=reconstructed,
                            enabled=enabled,
                        )
                    )
            else:
                expansions.append(
                    Expansion(
                        section=current_section,
                        trigger=trigger,
                        replacement=hotstring_match.group("replacement"),
                        enabled=enabled,
                    )
                )
            pending_source = None

    if not sections:
        sections.append("General")
    return ExpansionStore(
        sections=sections,
        expansions=expansions,
        variables=variables,
        templates=templates,
    )


_BLOCK_OPEN_RE = re.compile(r"^;?\s?\{\s*$")
_BLOCK_CLOSE_RE = re.compile(r"^;?\s?\}\s*$")
_AHK_QUOTED_RE = re.compile(r'"((?:`.|[^"`])*)"')

# Field entries inside a generated __tem_fields array. The key order is the one
# _form_fields_literal emits; the quoted-string subpattern is escape-aware so a
# prompt containing a quote or bracket does not end the match early.
_AHK_STR = r'"((?:`.|[^"`])*)"'
_FORM_FIELD_HEAD = (
    r'Map\("name", ' + _AHK_STR + r', "label", ' + _AHK_STR + r', "title", ' + _AHK_STR
)
_FORM_INPUT_FIELD_RE = re.compile(
    _FORM_FIELD_HEAD + r', "kind", "input", "default", ' + _AHK_STR + r"\)"
)
_FORM_SELECT_FIELD_RE = re.compile(
    _FORM_FIELD_HEAD
    + r', "kind", "select", "options", \[((?:'
    + _AHK_STR
    + r"(?:, )?)*)\]\)"
)
_FORM_FIELD_COUNT_RE = re.compile(r'Map\("name", ')


def _parse_form_fields(line: str) -> dict[str, dict[str, Any]] | None:
    """Rebuild the field table from a generated ``__tem_fields`` line.

    Returns None if any entry fails to match, so a block that is not in the
    generated form is refused rather than silently reconstructed short a field.
    """
    fields: dict[str, dict[str, Any]] = {}
    for match in _FORM_INPUT_FIELD_RE.finditer(line):
        name, label, title, default = (_unescape_ahk(g) for g in match.groups())
        fields[name] = {
            "name": name,
            "kind": "input",
            "label": label,
            "title": title,
            "default": default,
        }
    for match in _FORM_SELECT_FIELD_RE.finditer(line):
        name, label, title = (_unescape_ahk(g) for g in match.groups()[:3])
        fields[name] = {
            "name": name,
            "kind": "select",
            "label": label,
            "title": title,
            "options": [
                _unescape_ahk(option) for option in _AHK_QUOTED_RE.findall(match.group(4))
            ],
        }
    if len(fields) != len(_FORM_FIELD_COUNT_RE.findall(line)):
        return None
    return fields


def _form_field_placeholder(field: dict[str, Any]) -> str:
    if field["kind"] == "select":
        options = "||".join(field["options"])
        return f"{{AHK_SELECT:{field['name']}|{field['label']}|{field['title']}|{options}}}"
    return (
        f"{{AHK_INPUT:{field['name']}|{field['label']}|{field['title']}|{field['default']}}}"
    )


def _find_block_open(lines: list[str], index: int) -> int | None:
    """Return the index of a top-level ``{`` immediately following a hotstring."""
    for j in range(index + 1, len(lines)):
        if not lines[j].strip():
            continue
        return j if _BLOCK_OPEN_RE.match(lines[j]) else None
    return None


def _strip_disabled(line: str) -> str:
    return re.sub(r"^;\s?", "", line)


def _unescape_ahk(value: str) -> str:
    escapes = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "`": "`", '"': '"'}
    out: list[str] = []
    i = 0
    while i < len(value):
        char = value[i]
        if char == "`" and i + 1 < len(value):
            out.append(escapes.get(value[i + 1], value[i + 1]))
            i += 2
        else:
            out.append(char)
            i += 1
    return "".join(out)


def _reconstruct_replacement(lines: list[str], open_index: int) -> str | None:
    """Rebuild a replacement template from a generated code block.

    Reverses the fixed patterns emitted by ``render_expansion`` for dynamic
    expansions (literals, AHK_EXPR, AHK_INPUT, AHK_SELECT, AHK_KEY, AHK_IMAGE).
    Returns None if the block is not in the expected generated form.
    """
    close_index = None
    for j in range(open_index + 1, len(lines)):
        if _BLOCK_CLOSE_RE.match(lines[j]):
            close_index = j
            break
    if close_index is None:
        return None

    body = [_strip_disabled(lines[k]).strip() for k in range(open_index + 1, close_index)]
    parts: list[str] = []
    # Populated when the block gathers its prompts through TEM_Form, in which
    # case the placeholders are rebuilt at the "__tem_result .= <var>" lines
    # rather than at the prompt call itself.
    form_fields: dict[str, dict[str, Any]] = {}
    i = 0
    while i < len(body):
        line = body[i]
        if line in ("", '__tem_result := ""'):
            i += 1
            continue
        # Flush block: if (__tem_result != "") { ... }
        if line == 'if (__tem_result != "") {':
            i += 1
            while i < len(body) and body[i] != "}":
                i += 1
            i += 1
            continue
        # End-char handling (always a fixed 5-line trailer).
        if line.startswith("if (A_EndChar"):
            i += 5
            continue
        # Literal text: __tem_result .= "..."
        literal = re.fullmatch(r'__tem_result \.= "(.*)"', line)
        if literal:
            parts.append(_unescape_ahk(literal.group(1)))
            i += 1
            continue
        # Form block: the fields array carries every prompt's arguments; the
        # parts array and TEM_Form call add nothing the parts list needs.
        if line.startswith("__tem_fields := "):
            parsed = _parse_form_fields(line)
            if parsed is None:
                return None
            form_fields = parsed
            i += 1
            continue
        if line.startswith("__tem_parts := ") or line.startswith("__tem_vals := TEM_Form("):
            i += 1
            continue
        if re.fullmatch(r'\w+ := __tem_vals\["\w+"\]', line):
            i += 1
            continue
        # Input box: 5 lines.
        input_box = re.match(r"__tem_input_(\w+) := InputBox\(", line)
        if input_box:
            quoted = _AHK_QUOTED_RE.findall(line)
            if len(quoted) < 2:
                return None
            var = input_box.group(1)
            prompt = _unescape_ahk(quoted[0])
            title = _unescape_ahk(quoted[1])
            default = _unescape_ahk(quoted[2]) if len(quoted) > 2 else ""
            parts.append(f"{{AHK_INPUT:{var}|{prompt}|{title}|{default}}}")
            i += 5
            continue
        # List selection: 5 lines.
        selection = re.match(r"__tem_select_(\w+) := TEM_Select\(", line)
        if selection:
            quoted = _AHK_QUOTED_RE.findall(line)
            if len(quoted) < 2:
                return None
            var = selection.group(1)
            prompt = _unescape_ahk(quoted[0])
            title = _unescape_ahk(quoted[1])
            options = [_unescape_ahk(option) for option in quoted[2:]]
            parts.append(f"{{AHK_SELECT:{var}|{prompt}|{title}|{'||'.join(options)}}}")
            i += 5
            continue
        # Key press: SendEvent("{Tab}") followed by Sleep(...).
        key = re.fullmatch(r'SendEvent\("\{(\w+)\}"\)', line)
        if key:
            parts.append(f"{{AHK_KEY:{key.group(1)}}}")
            i += 1
            if i < len(body) and body[i].startswith("Sleep("):
                i += 1
            continue
        # Image paste: if (!TEM_PasteImage("...")) / return
        image = re.fullmatch(r'if \(!TEM_PasteImage\("(.*)"\)\)', line)
        if image:
            parts.append(f"{{AHK_IMAGE:{_unescape_ahk(image.group(1))}}}")
            i += 1
            if i < len(body) and body[i] == "return":
                i += 1
            continue
        # AHK expression: __tem_result .= <expr>
        expr = re.fullmatch(r"__tem_result \.= (.+)", line)
        if expr:
            value = expr.group(1).strip()
            if re.fullmatch(r"\w+", value):
                if value in form_fields:
                    # A form field: this is where its placeholder belongs, and a
                    # variable used twice correctly yields two placeholders.
                    parts.append(_form_field_placeholder(form_fields[value]))
                else:
                    # A bare variable reference already emitted by an input/select block.
                    i += 1
                    continue
                i += 1
                continue
            parts.append(f"{{AHK_EXPR:{value}}}")
            i += 1
            continue
        # Leftover lines that belong to an input/select block we already emitted.
        if (
            line == "return"
            or re.fullmatch(r"if \(.*\)", line)
            or re.fullmatch(r"\w+ := __tem_(input|select)_\w+\.\w+", line)
        ):
            i += 1
            continue
        # Unrecognised content: refuse rather than guess.
        return None

    return "".join(parts) if parts else None


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

    # Variables and templates form a shared, name-keyed library rather than
    # section-scoped entries. Add any the target lacks; leave existing
    # definitions of the same name untouched so a merge never clobbers them.
    existing_variable_names = {variable.name for variable in target.variables}
    for imported_variable in imported.variables:
        if imported_variable.name and imported_variable.name not in existing_variable_names:
            target.variables.append(VariableDef.from_dict(imported_variable.to_dict()))
            existing_variable_names.add(imported_variable.name)
            result.variables_added += 1

    existing_template_names = {template.name for template in target.templates}
    for imported_template in imported.templates:
        if imported_template.name and imported_template.name not in existing_template_names:
            target.templates.append(TemplateDef.from_dict(imported_template.to_dict()))
            existing_template_names.add(imported_template.name)
            result.templates_added += 1

    return result


def generate_ahk(store: ExpansionStore, path: Path, backup: bool = True) -> Path | None:
    validate_store_placeholders(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        backup_path = _backup_path(path)
        shutil.copy2(path, backup_path)
        _cleanup_old_backups(path)
    else:
        backup_path = None

    path.write_text(render_ahk(store), encoding="utf-8")
    return backup_path


def render_ahk(store: ExpansionStore) -> str:
    validate_store_placeholders(store)
    lines = [
        "#Requires AutoHotkey v2.0",
        "#SingleInstance Force",
        "; Generated by AutoHotkey Text Expansion Manager.",
        "; Edit expansions.json through the GUI, then regenerate this file.",
        "",
    ]
    if store.variables or store.templates:
        for variable in store.variables:
            lines.append(_variable_marker(variable))
        for template in store.templates:
            lines.append(_template_marker(template))
        lines.append("")
    rendered_sections: list[tuple[str, list[RenderedExpansion]]] = []
    needs_select_helper = False
    needs_image_helper = False
    needs_paste_helper = False
    needs_form_helper = False

    for section in store.sections:
        rendered_expansions = [
            render_expansion(expansion, store.variables, store.templates)
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
        needs_paste_helper = needs_paste_helper or any(
            item.needs_paste_helper for item in rendered_expansions
        )
        needs_form_helper = needs_form_helper or any(
            item.needs_form_helper for item in rendered_expansions
        )

    if needs_form_helper:
        lines.extend(_form_helper_lines())
        lines.append("")
    if needs_select_helper:
        lines.extend(_select_helper_lines())
        lines.append("")
    if needs_image_helper:
        lines.extend(_image_helper_lines())
        lines.append("")
    if needs_paste_helper:
        lines.extend(_paste_helper_lines())
        lines.append("")

    for section, rendered_expansions in rendered_sections:
        lines.append(f"; === {section} ===")
        if not rendered_expansions:
            lines.append("; No expansions in this section.")
        for rendered in rendered_expansions:
            lines.extend(rendered.lines)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _form_fields(segments: list[Any]) -> list[dict[str, Any]]:
    """Collect the prompted placeholders into one ordered list of form fields.

    A variable used more than once yields a single field -- the first occurrence
    defines its prompt, default and options -- so the form asks once and every
    occurrence receives the same answer.
    """
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for segment in segments:
        if isinstance(segment, str) or segment.kind not in ("AHK_INPUT", "AHK_SELECT"):
            continue
        variable = segment.args[0]
        if variable in seen:
            continue
        seen.add(variable)
        if segment.kind == "AHK_INPUT":
            _, prompt, title, default = segment.args
            fields.append(
                {
                    "name": variable,
                    "kind": "input",
                    "label": prompt,
                    "title": title,
                    "default": default,
                }
            )
        else:
            _, prompt, title, *options = segment.args
            fields.append(
                {
                    "name": variable,
                    "kind": "select",
                    "label": prompt,
                    "title": title,
                    "options": options,
                }
            )
    return fields


def _use_form(fields: list[dict[str, Any]]) -> bool:
    """Whether to gather these fields in one form dialog.

    An expansion whose only prompt is a single dropdown keeps the lighter
    TEM_Select popup; a whole form would be overkill for one pick. Everything
    else -- any text input, or more than one prompt -- gets the form, where the
    live preview supplies the context a bare prompt cannot.
    """
    if not fields:
        return False
    return not (len(fields) == 1 and fields[0]["kind"] == "select")


def _form_fields_literal(fields: list[dict[str, Any]]) -> str:
    """Emit the fields array TEM_Form builds its controls from.

    The title is emitted even though the dialog titles itself with the trigger
    and never draws it: it is the one placeholder argument the generated code
    would otherwise lose, and _reconstruct_replacement needs it to rebuild the
    template from a file whose @tem markers have been stripped.

    Key order is fixed because the reconstruction regexes match on it.
    """
    items = []
    for field in fields:
        head = (
            f'Map("name", {_ahk_string(field["name"])}, '
            f'"label", {_ahk_string(field["label"])}, '
            f'"title", {_ahk_string(field["title"])}'
        )
        if field["kind"] == "select":
            options = ", ".join(_ahk_string(option) for option in field["options"])
            items.append(f'{head}, "kind", "select", "options", [{options}])')
        else:
            items.append(
                f'{head}, "kind", "input", "default", {_ahk_string(field["default"])})'
            )
    return "[" + ", ".join(items) + "]"


def _form_parts_literal(segments: list[Any]) -> str:
    """Emit the parts array TEM_Form assembles its live preview from.

    Literals become strings and prompted placeholders become {var} references
    the preview substitutes as the user types. AHK_EXPR is emitted unquoted so
    it evaluates when the array is built -- the user sees the real date rather
    than the expression. Key presses and images have no text form, so they show
    as a bracketed chip standing in for the action.
    """
    items = []
    for segment in segments:
        if isinstance(segment, str):
            if segment:
                items.append(_ahk_string(segment))
        elif segment.kind == "AHK_EXPR":
            items.append(segment.value)
        elif segment.kind in ("AHK_INPUT", "AHK_SELECT"):
            items.append(f'Map("var", {_ahk_string(segment.args[0])})')
        elif segment.kind == "AHK_KEY":
            items.append(_ahk_string(f"[{segment.value}]"))
        elif segment.kind == "AHK_IMAGE":
            items.append(_ahk_string("[image]"))
    return "[" + ", ".join(items) + "]"


def render_expansion(
    expansion: Expansion,
    variables: list[VariableDef] | None = None,
    templates: list[TemplateDef] | None = None,
) -> RenderedExpansion:
    if expansion.replacement == "":
        # An empty replacement would emit ":opts:trigger::" with nothing after
        # "::", which AutoHotkey reads as the start of an execute hotstring and
        # then errors ("hotstring is missing its opening brace"). Emit an inert
        # comment instead so the generated script always runs.
        return RenderedExpansion([f'; Skipped "{expansion.trigger}": empty replacement.'])

    segments = resolve_template_segments(
        parse_replacement_template(expansion.replacement),
        templates or [],
    )
    segments = resolve_variable_segments(segments, variables or [])
    dynamic = any(isinstance(segment, TemplatePlaceholder) for segment in segments)
    # Paste delivery is auto-selected when the literal text contains a spaced or
    # double hyphen, which Word would otherwise autoformat into a dash. Only
    # literal string segments are inspected; placeholder argument text (prompts,
    # option lists) is not part of the emitted output.
    use_paste = any(
        isinstance(segment, str) and _needs_paste_delivery(segment)
        for segment in segments
    )

    if not dynamic:
        if use_paste:
            lines = [f":{HOTSTRING_OPTIONS}:{expansion.trigger}::", "{"]
            lines.append(f"    TEM_Paste({_ahk_string(expansion.replacement)})")
            lines.extend(_end_char_lines())
            lines.append("}")
        else:
            lines = [
                f":{STATIC_HOTSTRING_OPTIONS}:{expansion.trigger}::{_single_line_replacement(expansion.replacement)}"
            ]
        if expansion.notes:
            lines.append(f"; Notes: {expansion.notes}")
        body = [_source_marker(expansion), *_maybe_disable_lines(lines, expansion.enabled)]
        return RenderedExpansion(body, needs_paste_helper=use_paste)

    lines = [f":{HOTSTRING_OPTIONS}:{expansion.trigger}::", "{"]
    lines.append("    __tem_result := \"\"")
    needs_select_helper = False
    needs_image_helper = False
    send_call = "TEM_Paste(__tem_result)" if use_paste else "SendText(__tem_result)"

    # Every prompt is gathered up front in one dialog, so the user answers with
    # the whole resolved text in view and can revise any field before inserting.
    fields = _form_fields(segments)
    use_form = _use_form(fields)
    if use_form:
        lines.append(f"    __tem_fields := {_form_fields_literal(fields)}")
        lines.append(f"    __tem_parts := {_form_parts_literal(segments)}")
        lines.append(
            f"    __tem_vals := TEM_Form({_ahk_string(expansion.trigger)}, __tem_fields, __tem_parts)"
        )
        lines.append("    if (!IsObject(__tem_vals))")
        lines.append("        return")
        for field in fields:
            lines.append(
                f'    {field["name"]} := __tem_vals[{_ahk_string(field["name"])}]'
            )

    def flush_result() -> None:
        lines.append("    if (__tem_result != \"\") {")
        lines.append(f"        {send_call}")
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
            # The form already assigned the variable, including for a repeat
            # occurrence, so the value is only appended here.
            variable, prompt, title, default = segment.args
            if not use_form:
                lines.append(f"    __tem_input_{variable} := InputBox({_ahk_string(prompt)}, {_ahk_string(title)}, , {_ahk_string(default)})")
                lines.append(f"    if (__tem_input_{variable}.Result = \"Cancel\")")
                lines.append("        return")
                lines.append(f"    {variable} := __tem_input_{variable}.Value")
            lines.append(f"    __tem_result .= {variable}")
        elif segment.kind == "AHK_SELECT":
            variable, prompt, title, *options = segment.args
            if not use_form:
                option_list = ", ".join(_ahk_string(option) for option in options)
                lines.append(f"    __tem_select_{variable} := TEM_Select({_ahk_string(prompt)}, {_ahk_string(title)}, [{option_list}])")
                lines.append(f"    if (!__tem_select_{variable}.ok)")
                lines.append("        return")
                lines.append(f"    {variable} := __tem_select_{variable}.value")
                needs_select_helper = True
            lines.append(f"    __tem_result .= {variable}")
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
    lines.extend(_end_char_lines())
    lines.append("}")
    if expansion.notes:
        lines.append(f"; Notes: {expansion.notes}")
    body = [_source_marker(expansion), *_maybe_disable_lines(lines, expansion.enabled)]
    return RenderedExpansion(
        body,
        needs_select_helper,
        needs_image_helper,
        use_paste,
        use_form,
    )


def resolve_expansion_preview(expansion: Expansion, store: ExpansionStore) -> PreviewResult:
    raw_segments = parse_replacement_template(expansion.replacement)
    resolved_segments = resolve_preview_segments(raw_segments, store)
    generated = render_expansion(expansion, store.variables, store.templates)
    content = "\n".join(
        [
            "Expansion Preview",
            "=================",
            "",
            f"Section: {expansion.section}",
            f"Trigger: {expansion.trigger}",
            f"Enabled: {'Yes' if expansion.enabled else 'No'}",
            "",
            "Raw Replacement Text",
            "--------------------",
            expansion.replacement or "",
            "",
            "Resolved Replacement Text",
            "-------------------------",
            segments_to_readable_text(resolved_segments),
            "",
            "Placeholder Summary",
            "-------------------",
            collect_placeholder_summary(raw_segments, store),
            "",
            "Generated AutoHotkey v2 Code",
            "----------------------------",
            "\n".join(generated.lines),
        ]
    )
    return PreviewResult(f"Expansion: {expansion.trigger}", content)


def resolve_variable_preview(variable: VariableDef) -> PreviewResult:
    placeholder = variable_to_placeholder(variable)
    resolved = placeholder_to_text(placeholder)
    dynamic = "Yes" if placeholder.kind in {"AHK_INPUT", "AHK_SELECT", "AHK_EXPR"} else "No"
    options = "\n".join(f"- {option}" for option in variable.list_options) or "(none)"
    content = "\n".join(
        [
            "Variable Preview",
            "================",
            "",
            f"Name: {variable.name}",
            f"Type: {variable.type}",
            f"Prompt text: {variable.prompt_text or '(none)'}",
            f"Default value: {variable.default_value or '(none)'}",
            "",
            "List Options",
            "------------",
            options,
            "",
            f"Example placeholder: {{VAR:{variable.name}}}",
            f"Resolved placeholder form: {resolved}",
            f"Requires dynamic runtime generation: {dynamic}",
        ]
    )
    return PreviewResult(f"Variable: {variable.name}", content)


def resolve_template_preview(template: TemplateDef, store: ExpansionStore) -> PreviewResult:
    raw_segments = parse_replacement_template(template.body)
    resolved_segments = resolve_preview_segments(raw_segments, store, stack=(template.name,))
    content = "\n".join(
        [
            "Template Preview",
            "================",
            "",
            f"Name: {template.name}",
            f"Description: {template.description or '(none)'}",
            "",
            "Raw Template Body",
            "-----------------",
            template.body or "",
            "",
            "Resolved Template Body",
            "----------------------",
            segments_to_readable_text(resolved_segments),
            "",
            "Placeholder Summary",
            "-------------------",
            collect_placeholder_summary(raw_segments, store, stack=(template.name,)),
        ]
    )
    return PreviewResult(f"Template: {template.name}", content)


def resolve_preview_segments(
    segments: list[str | TemplatePlaceholder],
    store: ExpansionStore,
    stack: tuple[str, ...] = (),
) -> list[str | TemplatePlaceholder]:
    template_map = {template.name: template for template in store.templates}
    resolved: list[str | TemplatePlaceholder] = []
    for segment in segments:
        if isinstance(segment, str):
            resolved.append(segment)
        elif segment.kind == "VAR":
            variable = store.variable_by_name(segment.value)
            if variable is None:
                raise ValueError(f'Undefined variable "{segment.value}".')
            resolved.append(variable_to_placeholder(variable))
        elif segment.kind == "TPL":
            template = template_map.get(segment.value)
            if template is None:
                raise ValueError(f'Undefined template "{segment.value}".')
            if segment.value in stack:
                cycle = " -> ".join([*stack, segment.value])
                raise ValueError(f"Circular template reference detected: {cycle}.")
            resolved.extend(
                resolve_preview_segments(
                    parse_replacement_template(template.body),
                    store,
                    (*stack, segment.value),
                )
            )
        else:
            resolved.append(segment)
    return resolved


def collect_placeholder_summary(
    segments: list[str | TemplatePlaceholder],
    store: ExpansionStore | None = None,
    stack: tuple[str, ...] = (),
) -> str:
    found = {
        "Variables": [],
        "Date/Time": 0,
        "Input boxes": 0,
        "List selections": 0,
        "Keystrokes": [],
        "Images": [],
        "Nested templates": [],
    }
    _collect_placeholder_summary(segments, found, store, stack)
    lines: list[str] = []
    if found["Variables"]:
        lines.append(f"- Variables: {', '.join(dict.fromkeys(found['Variables']))}")
    if found["Date/Time"]:
        lines.append(f"- Date/Time: {found['Date/Time']}")
    if found["Input boxes"]:
        lines.append(f"- Input boxes: {found['Input boxes']}")
    if found["List selections"]:
        lines.append(f"- List selections: {found['List selections']}")
    if found["Keystrokes"]:
        lines.append(f"- Keystrokes: {', '.join(dict.fromkeys(found['Keystrokes']))}")
    if found["Images"]:
        lines.append(f"- Images: {', '.join(dict.fromkeys(found['Images']))}")
    if found["Nested templates"]:
        lines.append(f"- Nested templates: {', '.join(dict.fromkeys(found['Nested templates']))}")
    return "\n".join(lines) if lines else "No placeholders found."


def _collect_placeholder_summary(
    segments: list[str | TemplatePlaceholder],
    found: dict[str, Any],
    store: ExpansionStore | None,
    stack: tuple[str, ...],
) -> None:
    for segment in segments:
        if isinstance(segment, str):
            continue
        if segment.kind == "VAR":
            found["Variables"].append(segment.value)
            if store:
                variable = store.variable_by_name(segment.value)
                if variable:
                    _collect_placeholder_summary([variable_to_placeholder(variable)], found, store, stack)
        elif segment.kind == "TPL":
            found["Nested templates"].append(segment.value)
            if store:
                template = store.template_by_name(segment.value)
                if template:
                    if segment.value in stack:
                        cycle = " -> ".join([*stack, segment.value])
                        raise ValueError(f"Circular template reference detected: {cycle}.")
                    _collect_placeholder_summary(
                        parse_replacement_template(template.body),
                        found,
                        store,
                        (*stack, segment.value),
                    )
        elif segment.kind == "AHK_EXPR":
            found["Date/Time"] += 1
        elif segment.kind == "AHK_INPUT":
            found["Input boxes"] += 1
        elif segment.kind == "AHK_SELECT":
            found["List selections"] += 1
        elif segment.kind == "AHK_KEY":
            found["Keystrokes"].append(segment.value)
        elif segment.kind == "AHK_IMAGE":
            found["Images"].append(segment.value)


def segments_to_readable_text(segments: list[str | TemplatePlaceholder]) -> str:
    return "".join(segment if isinstance(segment, str) else placeholder_to_text(segment) for segment in segments)


def placeholder_to_text(placeholder: TemplatePlaceholder) -> str:
    if placeholder.kind == "AHK_EXPR":
        return f"{{AHK_EXPR:{placeholder.value}}}"
    if placeholder.kind == "AHK_INPUT":
        variable, prompt, title, default = placeholder.args
        return f"{{AHK_INPUT:{variable}|{prompt}|{title}|{default}}}"
    if placeholder.kind == "AHK_SELECT":
        variable, prompt, title, *options = placeholder.args
        return f"{{AHK_SELECT:{variable}|{prompt}|{title}|{'||'.join(options)}}}"
    if placeholder.kind in {"AHK_KEY", "AHK_IMAGE", "VAR", "TPL"}:
        return f"{{{placeholder.kind}:{placeholder.value}}}"
    return f"{{{placeholder.kind}:{placeholder.value}}}"


def validate_store_placeholders(store: ExpansionStore) -> None:
    validate_variables(store.variables)
    validate_templates(store.templates)
    for expansion in store.expansions:
        try:
            segments = resolve_template_segments(
                parse_replacement_template(expansion.replacement),
                store.templates,
            )
            resolve_variable_segments(segments, store.variables)
        except ValueError as exc:
            raise ValueError(f'Invalid placeholder in trigger "{expansion.trigger}": {exc}') from exc
    for template in store.templates:
        try:
            segments = resolve_template_segments(
                parse_replacement_template(template.body),
                store.templates,
                stack=(template.name,),
            )
            resolve_variable_segments(segments, store.variables)
        except ValueError as exc:
            raise ValueError(f'Invalid placeholder in template "{template.name}": {exc}') from exc


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

    if kind == "VAR":
        variable_name = body.strip()
        if not variable_name:
            raise ValueError("VAR requires a variable name.")
        _validate_variable_name(variable_name, "VAR")
        return TemplatePlaceholder(kind, variable_name, [])

    if kind == "TPL":
        template_name = body.strip()
        if not template_name:
            raise ValueError("TPL requires a template name.")
        return TemplatePlaceholder(kind, template_name, [])

    raise ValueError(f"Unsupported placeholder type {kind}.")


def resolve_template_segments(
    segments: list[str | TemplatePlaceholder],
    templates: list[TemplateDef],
    stack: tuple[str, ...] = (),
) -> list[str | TemplatePlaceholder]:
    template_map = {template.name: template for template in templates}
    resolved: list[str | TemplatePlaceholder] = []
    for segment in segments:
        if not isinstance(segment, TemplatePlaceholder) or segment.kind != "TPL":
            resolved.append(segment)
            continue
        template = template_map.get(segment.value)
        if template is None:
            raise ValueError(f'Undefined template "{segment.value}".')
        if segment.value in stack:
            cycle = " -> ".join([*stack, segment.value])
            raise ValueError(f"Circular template reference detected: {cycle}.")
        nested_segments = resolve_template_segments(
            parse_replacement_template(template.body),
            templates,
            (*stack, segment.value),
        )
        resolved.extend(nested_segments)
    return resolved


def resolve_variable_segments(
    segments: list[str | TemplatePlaceholder],
    variables: list[VariableDef],
) -> list[str | TemplatePlaceholder]:
    variable_map = {variable.name: variable for variable in variables}
    resolved: list[str | TemplatePlaceholder] = []
    for segment in segments:
        if not isinstance(segment, TemplatePlaceholder) or segment.kind != "VAR":
            resolved.append(segment)
            continue
        variable = variable_map.get(segment.value)
        if variable is None:
            raise ValueError(f'Undefined variable "{segment.value}".')
        resolved.append(variable_to_placeholder(variable))
    return resolved


def variable_to_placeholder(variable: VariableDef) -> TemplatePlaceholder:
    validate_variable(variable)
    if variable.type == "text_input":
        prompt = variable.prompt_text or f"Enter {variable.name}"
        title = variable.name.replace("_", " ").title()
        return TemplatePlaceholder(
            "AHK_INPUT",
            "",
            [variable.name, prompt, title, variable.default_value],
        )
    if variable.type == "list_selection":
        prompt = variable.prompt_text or f"Choose {variable.name}"
        title = variable.name.replace("_", " ").title()
        return TemplatePlaceholder(
            "AHK_SELECT",
            "",
            [variable.name, prompt, title, *variable.list_options],
        )
    if variable.type == "date_time":
        date_format = variable.default_value or "yyyy-MM-dd"
        return TemplatePlaceholder(
            "AHK_EXPR",
            f'FormatTime(A_Now, "{date_format}")',
            [],
        )
    raise ValueError(f'Unsupported variable type "{variable.type}".')


def validate_variables(variables: list[VariableDef]) -> None:
    seen: set[str] = set()
    for variable in variables:
        validate_variable(variable)
        if variable.name in seen:
            raise ValueError(f'Duplicate variable name "{variable.name}".')
        seen.add(variable.name)


def validate_variable(variable: VariableDef) -> None:
    if not variable.name:
        raise ValueError("Variable name cannot be blank.")
    _validate_variable_name(variable.name, "Variable")
    if variable.type not in VARIABLE_TYPES:
        raise ValueError(f'Unsupported variable type "{variable.type}".')
    if variable.type == "list_selection" and not variable.list_options:
        raise ValueError(f'Variable "{variable.name}" requires at least one list option.')
    if variable.type == "date_time" and any(char in variable.default_value for char in '{}"'):
        raise ValueError(f'Date/time variable "{variable.name}" format cannot contain braces or double quotes.')


def validate_templates(templates: list[TemplateDef]) -> None:
    seen: set[str] = set()
    for template in templates:
        validate_template(template)
        if template.name in seen:
            raise ValueError(f'Duplicate template name "{template.name}".')
        seen.add(template.name)


def validate_template(template: TemplateDef) -> None:
    if not template.name:
        raise ValueError("Template name cannot be blank.")


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


def _source_marker(expansion: Expansion) -> str:
    payload: dict[str, str] = {"replacement": expansion.replacement}
    if expansion.notes:
        payload["notes"] = expansion.notes
    return "; @tem: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _variable_marker(variable: VariableDef) -> str:
    return "; @tem-var: " + json.dumps(
        variable.to_dict(), ensure_ascii=False, separators=(",", ":")
    )


def _template_marker(template: TemplateDef) -> str:
    return "; @tem-template: " + json.dumps(
        template.to_dict(), ensure_ascii=False, separators=(",", ":")
    )


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


def _form_helper_lines() -> list[str]:
    """One dialog gathering every prompt, above a preview of the resolved text.

    updatePreview reads the live controls on each keystroke rather than closing
    over per-field state, which also sidesteps the v2 closure-in-a-loop trap
    where every handler would otherwise capture the last field.
    """
    return [
        "TEM_Form(title, fields, parts) {",
        "    formGui := Gui(\"+AlwaysOnTop +OwnDialogs\", title)",
        "    formGui.SetFont(\"s9\")",
        "    formGui.AddText(\"xm w460\", \"Preview\")",
        "    preview := formGui.AddEdit(\"xm w460 r4 Multi ReadOnly\")",
        "    controls := Map()",
        "    state := Map(\"ok\", false, \"values\", \"\")",
        "    updatePreview() {",
        "        text := \"\"",
        "        for part in parts {",
        "            if (part is Map)",
        "                text .= controls.Has(part[\"var\"]) ? controls[part[\"var\"]].Text : \"\"",
        "            else",
        "                text .= part",
        "        }",
        "        preview.Value := text",
        "    }",
        "    for field in fields {",
        "        formGui.AddText(\"xm w140 Right\", field[\"label\"])",
        "        if (field[\"kind\"] = \"select\")",
        "            ctrl := formGui.AddDropDownList(\"x+8 yp-4 w312 Choose1\", field[\"options\"])",
        "        else",
        "            ctrl := formGui.AddEdit(\"x+8 yp-4 w312\", field[\"default\"])",
        "        ctrl.OnEvent(\"Change\", (*) => updatePreview())",
        "        controls[field[\"name\"]] := ctrl",
        "    }",
        "    okButton := formGui.AddButton(\"xm+272 y+16 w90 Default\", \"Insert\")",
        "    cancelButton := formGui.AddButton(\"x+8 w90\", \"Cancel\")",
        "    okButton.OnEvent(\"Click\", (*) => (state[\"ok\"] := true, state[\"values\"] := TEM_FormValues(fields, controls), formGui.Destroy()))",
        "    cancelButton.OnEvent(\"Click\", (*) => formGui.Destroy())",
        "    formGui.OnEvent(\"Close\", (*) => formGui.Destroy())",
        "    formGui.OnEvent(\"Escape\", (*) => formGui.Destroy())",
        "    updatePreview()",
        "    formGui.Show()",
        "    if (fields.Length)",
        "        controls[fields[1][\"name\"]].Focus()",
        "    guiHwnd := formGui.Hwnd",
        "    WinWaitClose(\"ahk_id \" guiHwnd)",
        "    return state[\"ok\"] ? state[\"values\"] : \"\"",
        "}",
        "",
        "TEM_FormValues(fields, controls) {",
        "    values := Map()",
        "    for field in fields",
        "        values[field[\"name\"]] := controls[field[\"name\"]].Text",
        "    return values",
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


def _end_char_lines() -> list[str]:
    # Dynamic hotstrings run code instead of auto-replacing, so AutoHotkey does
    # not reproduce the ending character (space/Enter/Tab) that triggered them.
    # Re-send A_EndChar so it is preserved, matching plain-text hotstrings.
    return [
        '    if (A_EndChar = "`r" || A_EndChar = "`n") {',
        '        Send("{Enter}")',
        '    } else if (A_EndChar != "") {',
        "        SendText(A_EndChar)",
        "    }",
    ]


def _single_line_replacement(text: str) -> str:
    return text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


# Word's "AutoFormat As You Type" rewrites a hyphen followed by a space ("- ",
# which also covers a spaced hyphen " - " and a leading "- ") into a dash, and a
# double hyphen ("--") into an em dash, when text is *typed*. Pasting bypasses
# that, so literal text matching these patterns is delivered via TEM_Paste.
def _needs_paste_delivery(text: str) -> bool:
    return "- " in text or "--" in text


def _paste_helper_lines() -> list[str]:
    return [
        "TEM_Paste(text) {",
        "    if (text = \"\")",
        "        return",
        "    saved := ClipboardAll()",
        "    A_Clipboard := text",
        "    if (!ClipWait(1)) {",
        "        A_Clipboard := saved",
        "        saved := \"\"",
        "        SendText(text)",
        "        return",
        "    }",
        "    Send(\"^v\")",
        "    Sleep(120)",
        "    A_Clipboard := saved",
        "    saved := \"\"",
        "}",
    ]


def _backup_path(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffixes = _existing_backup_suffixes(path, timestamp)
    if not suffixes:
        return path.with_name(f"{path.stem}.{timestamp}.bak{path.suffix}")
    next_suffix = max(suffixes) + 1
    return path.with_name(f"{path.stem}.{timestamp}_{next_suffix}.bak{path.suffix}")


def _cleanup_old_backups(path: Path, keep: int = BACKUP_RETENTION_LIMIT) -> None:
    backups = _app_backup_paths(path)
    if len(backups) <= keep:
        return

    backups.sort(key=_backup_sort_key, reverse=True)
    for backup_path in backups[keep:]:
        backup_path.unlink()


def _app_backup_paths(path: Path) -> list[Path]:
    escaped_stem = re.escape(path.stem)
    escaped_suffix = re.escape(path.suffix)
    backup_re = re.compile(
        rf"^{escaped_stem}\.\d{{8}}_\d{{6}}(?:_\d+)?\.bak{escaped_suffix}$"
    )
    return [
        candidate
        for candidate in path.parent.iterdir()
        if candidate.is_file() and backup_re.match(candidate.name)
    ]


def _backup_sort_key(path: Path) -> tuple[str, int, str]:
    match = re.search(r"\.(\d{8}_\d{6})(?:_(\d+))?\.bak", path.name)
    if not match:
        return ("", 0, path.name)
    suffix = int(match.group(2) or "1")
    return (match.group(1), suffix, path.name)


def _existing_backup_suffixes(path: Path, timestamp: str) -> list[int]:
    escaped_stem = re.escape(path.stem)
    escaped_suffix = re.escape(path.suffix)
    backup_re = re.compile(
        rf"^{escaped_stem}\.{re.escape(timestamp)}(?:_(\d+))?\.bak{escaped_suffix}$"
    )
    suffixes: list[int] = []
    for candidate in path.parent.iterdir():
        if not candidate.is_file():
            continue
        match = backup_re.match(candidate.name)
        if match:
            suffixes.append(int(match.group(1) or "1"))
    return suffixes


def _find_expansion(store: ExpansionStore, section: str, trigger: str) -> Expansion | None:
    for expansion in store.expansions:
        if expansion.section == section and expansion.trigger == trigger:
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
