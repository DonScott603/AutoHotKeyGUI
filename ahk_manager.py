import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypedDict, cast


DEFAULT_JSON = "expansions.json"
DEFAULT_AHK = "text_expansions.ahk"
DEFAULT_SETTINGS = "settings.json"
BACKUP_RETENTION_LIMIT = 10

# Prompt windows are branded with the app rather than the bare trigger, so a
# popup that appears mid-typing is identifiable in the taskbar and Alt-Tab.
APP_TITLE = "Text Expansion Manager"

# Copied next to the generated script at generate time: the script is run by
# AutoHotkey.exe on its own, so it cannot reach an icon bundled inside our exe.
AHK_ICON_NAME = "TextExpansionManager.ico"

# The subset of the app palette the prompts can actually use. Mirrors the
# matching keys in app.py's _THEME_COLORS; AHK wants bare RRGGBB, no leading #.
AHK_THEME_COLORS = {
    "light": {"bg": "f4f5f7", "field": "ffffff", "text": "1f2937"},
    "dark": {"bg": "1e1f22", "field": "2b2d31", "text": "e5e7eb"},
}
DEFAULT_THEME = "light"


def ahk_theme_colors(theme: str) -> dict[str, str]:
    """Palette for a theme name, falling back to light for anything unknown."""
    return AHK_THEME_COLORS.get(theme, AHK_THEME_COLORS[DEFAULT_THEME])


def _prompt_title(trigger: str) -> str:
    """Window title for the prompt raised by a trigger."""
    return f"{APP_TITLE} - {trigger}"


@dataclass
class AppSettings:
    generated_ahk_path: str
    # Blank means the application's default backup folder. Storing the choice
    # rather than the resolved path keeps a default-configured install working
    # after it is moved.
    backup_directory: str = ""

    @classmethod
    def load(cls, path: Path, default_ahk_path: Path) -> "AppSettings":
        if not path.exists():
            return cls(str(default_ahk_path))

        try:
            with path.open("r", encoding="utf-8") as handle:
                parsed: object = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not load {path.name}: {exc}") from exc

        # Valid JSON that is not an object would reach .get and raise
        # AttributeError, which callers do not catch. Refuse it as a load error
        # so the caller's recovery path handles it like any other bad file.
        if not isinstance(parsed, dict):
            raise ValueError(
                f"Could not load {path.name}: expected a JSON object, "
                f"found {type(parsed).__name__}."
            )
        # The isinstance check earns the dict; the key and value types are still
        # whatever was on disk, which is what every read below assumes.
        data = cast(dict[str, Any], parsed)

        # Both fields become filesystem paths, so str() is worse here than it
        # is for a record: an array does not fail, it becomes a directory named
        # "['backups']" that start-up then creates and migrates backups into.
        # Absent and null still fall back to the defaults below, which is how
        # they have always been read.
        problem = _record_problem(data, "settings")
        if problem:
            raise ValueError(f"Could not load {path.name}: {problem}.")

        configured_path = str(data.get("generated_ahk_path") or "").strip()
        backup_directory = str(data.get("backup_directory") or "").strip()
        return cls(configured_path or str(default_ahk_path), backup_directory)

    def save(self, path: Path) -> None:
        data = {
            "generated_ahk_path": self.generated_ahk_path,
            "backup_directory": self.backup_directory,
        }
        _atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


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


def _collection_field(data: dict[str, Any], key: str, filename: str) -> list[Any]:
    """The named collection, or a load error naming what was found instead.

    save writes every one of these fields as a JSON array. Another type reaches
    a for loop and raises TypeError, which -- unlike the ValueError the rest of
    this loader raises -- no caller catches, so the window never opens at all.
    The types that do iterate are worse than the crash: an object yields its
    keys and a string yields one character at a time, both without complaint,
    and the next autosave writes that result back over the original.
    """
    value = data.get(key, [])
    if not isinstance(value, list):
        raise ValueError(
            f'Could not load {filename}: "{key}" must be a JSON array, '
            f"found {type(value).__name__}."
        )
    return cast(list[Any], value)


# What each record's fields have to be on disk. from_dict coerces whatever it
# finds -- str() turns an object into its Python repr and bool() turns the
# string "false" into True -- and the coerced value is what the next autosave
# writes back, so the original is gone. Checked here instead, before from_dict
# sees it.
#
# "text" tolerates null as a way of writing "not set", which is how from_dict
# has always read it. "bool" does not: bool(None) is False, so a null enabled
# would quietly disable an expansion. "text_list" also accepts a single string,
# the newline-separated form VariableDef.from_dict already splits.
_FIELD_TYPES = {
    "settings": {
        "generated_ahk_path": "text", "backup_directory": "text",
    },
    "expansions": {
        "section": "text", "trigger": "text", "replacement": "text",
        "enabled": "bool", "notes": "text",
    },
    "variables": {
        "name": "text", "type": "text", "prompt_text": "text",
        "default_value": "text", "list_options": "text_list", "notes": "text",
    },
    "templates": {
        "name": "text", "description": "text", "body": "text", "notes": "text",
    },
}
def _field_problem(value: Any, expected: str) -> str | None:
    """What is wrong with this field, phrased to follow its name, or None."""
    if expected == "text":
        if value is None or isinstance(value, str):
            return None
        return f"must be a string, found {type(value).__name__}"
    if expected == "bool":
        # isinstance(1, bool) is False, so a JSON number is refused here even
        # though Python would happily treat it as truthy.
        if isinstance(value, bool):
            return None
        return f"must be true or false, found {type(value).__name__}"
    if value is None or isinstance(value, str):
        return None
    if not isinstance(value, list):
        return f"must be an array of strings, found {type(value).__name__}"
    for position, item in enumerate(cast(list[Any], value)):
        if not isinstance(item, str):
            # Naming the position matters here: the field itself is the right
            # type and only one of its entries is not.
            return (
                f"must be an array of strings, but entry {position + 1} is "
                f"{type(item).__name__}"
            )
    return None


def _entry_dicts(data: dict[str, Any], key: str, filename: str) -> list[dict[str, Any]]:
    """The entries of a record collection, each confirmed to be a usable object.

    A malformed entry is refused rather than skipped. Skipping keeps the file
    open, but the entry is gone for good as soon as autosave rewrites the file
    in the normalised schema, whereas a load error leaves the original on disk
    and routes to the backup restore on the Help page.

    Fields the schema does not name are ignored rather than refused, so a file
    written by a later version opens. That is not forward compatibility, and
    was described as such here until it was measured: the values are not
    carried on the dataclasses and save rebuilds each record from the known
    fields, so the first autosave drops them. Opening a newer library in an
    older build and changing anything discards whatever the newer build added.

    Left as it is deliberately. Preserving unknown values would mean carrying
    them through every record, and refusing a newer file outright would lock
    the user out of their own library over a field this build has no opinion
    about. The behaviour is recorded here, and pinned by a test, rather than
    claimed to be something it is not.
    """
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(_collection_field(data, key, filename)):
        if not isinstance(item, dict):
            raise ValueError(
                f'Could not load {filename}: "{key}" entry {index + 1} must be '
                f"a JSON object, found {type(item).__name__}."
            )
        entry = cast(dict[str, Any], item)
        problem = _record_problem(entry, key)
        if problem:
            raise ValueError(
                f'Could not load {filename}: "{key}" entry {index + 1} {problem}.'
            )
        entries.append(entry)
    return entries


def _record_problem(entry: dict[str, Any], kind: str) -> str | None:
    """The first field of this record that is not the type it is written as.

    Shared with the marker importer: a record reaches the library from a JSON
    collection or from a comment in a generated script, and both end up merged
    and autosaved, so both have to be held to the same shape.
    """
    for field, expected in _FIELD_TYPES[kind].items():
        if field not in entry:
            continue
        problem = _field_problem(entry[field], expected)
        if problem:
            return f'field "{field}" {problem}'
    return None


def _section_names(data: dict[str, Any], filename: str) -> list[str]:
    """The section names, each confirmed to be a string.

    str() accepts anything and coerces it into a plausible-looking name -- 4
    becomes "4", an object becomes its Python repr -- so the wrong shape has to
    be refused before the conversion rather than after it. Blank names are
    still dropped: that is normalisation, not corruption.
    """
    names: list[str] = []
    for index, item in enumerate(_collection_field(data, "sections", filename)):
        if not isinstance(item, str):
            raise ValueError(
                f'Could not load {filename}: "sections" entry {index + 1} must '
                f"be a string, found {type(item).__name__}."
            )
        if item.strip():
            names.append(item.strip())
    return names


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
                parsed: object = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not load {path.name}: {exc}") from exc

        # See AppSettings.load: a JSON array, string, number or null parses
        # cleanly and then blows up on .get with an AttributeError the caller
        # does not expect. The app falls back to an empty store on ValueError,
        # which is the behaviour a corrupt file should get.
        if not isinstance(parsed, dict):
            raise ValueError(
                f"Could not load {path.name}: expected a JSON object, "
                f"found {type(parsed).__name__}."
            )
        data = cast(dict[str, Any], parsed)

        # The isinstance check above earns the outer object; the collections
        # inside it are still whatever was on disk, and each one is iterated
        # directly below.
        sections = _section_names(data, path.name)
        expansions = [
            Expansion.from_dict(item)
            for item in _entry_dicts(data, "expansions", path.name)
        ]
        variables = [
            VariableDef.from_dict(item)
            for item in _entry_dicts(data, "variables", path.name)
        ]
        templates = [
            TemplateDef.from_dict(item)
            for item in _entry_dicts(data, "templates", path.name)
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
        _atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")

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
    # Variables and templates that already existed by name. Counted together
    # because the choice is made once for both, and the caller reports them
    # the same way.
    definitions_overwritten: int = 0
    definitions_renamed: int = 0
    definitions_skipped: int = 0

    @property
    def total_changed(self) -> int:
        return self.added + self.overwritten + self.renamed


@dataclass
class ImportConflicts:
    """What the imported file already has counterparts for here.

    Split because the two read differently to someone deciding: a trigger
    conflict affects that expansion, and a definition conflict can reach every
    expansion already in the library that uses the name.
    """

    triggers: int = 0
    definitions: int = 0

    @property
    def total(self) -> int:
        return self.triggers + self.definitions

    def __bool__(self) -> bool:
        return self.total > 0


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
# An expansion that generates no hotstring at all has nothing for the source
# marker above to attach to, so it carries its whole record instead -- section
# and trigger included -- the same way the two markers above do. Without it a
# skipped expansion was simply absent from a re-import and quietly lost.
SKIPPED_MARKER_RE = re.compile(r"^\s*;\s*@tem-skipped:\s*(?P<json>.*)$")
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


def _marker_record(
    json_text: str, marker: str, path: Path, line_number: int, kind: str
) -> dict[str, Any]:
    """The record a marker line carries, or an import error placing the fault.

    Refused rather than skipped or coerced. The markers used to go straight to
    from_dict, which takes whatever it finds -- bool("false") is True and str()
    turns an object into its Python repr -- while ExpansionStore.load checked
    the same fields first. Both routes end with the record merged into the live
    library and autosaved, so the lenient one decided what ended up on disk.
    """
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Could not import {path.name}: {marker} on line {line_number} is "
            f"not valid JSON: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"Could not import {path.name}: {marker} on line {line_number} "
            f"must be a JSON object, found {type(data).__name__}."
        )
    record = cast(dict[str, Any], data)
    problem = _record_problem(record, kind)
    if problem:
        raise ValueError(
            f"Could not import {path.name}: {marker} on line {line_number} "
            f"{problem}."
        )
    return record


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
            variable = VariableDef.from_dict(
                _marker_record(
                    var_match.group("json"), "@tem-var", path, index + 1, "variables"
                )
            )
            try:
                validate_variable(variable)
            except ValueError as exc:
                raise ValueError(
                    f"Could not import {path.name}: @tem-var on line "
                    f"{index + 1}: {exc}"
                ) from exc
            variables.append(variable)
            continue

        template_match = TEMPLATE_MARKER_RE.match(line)
        if template_match:
            template = TemplateDef.from_dict(
                _marker_record(
                    template_match.group("json"),
                    "@tem-template",
                    path,
                    index + 1,
                    "templates",
                )
            )
            try:
                validate_template(template)
            except ValueError as exc:
                raise ValueError(
                    f"Could not import {path.name}: @tem-template on line "
                    f"{index + 1}: {exc}"
                ) from exc
            templates.append(template)
            continue

        skipped_match = SKIPPED_MARKER_RE.match(line)
        if skipped_match:
            skipped = Expansion.from_dict(
                _marker_record(
                    skipped_match.group("json"),
                    "@tem-skipped",
                    path,
                    index + 1,
                    "expansions",
                ),
                current_section,
            )
            if skipped.trigger:
                # The record carries its own section, so it lands where it
                # started even though no "; === ... ===" header precedes it.
                if skipped.section not in sections:
                    sections.append(skipped.section)
                expansions.append(skipped)
            continue

        marker_match = SOURCE_MARKER_RE.match(line)
        if marker_match:
            pending_source = _marker_record(
                marker_match.group("json"), "@tem", path, index + 1, "expansions"
            )
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
                        # AHK reads escapes in the replacement text, so the
                        # stored template is the unescaped form -- both for our
                        # own output and for a hand-written script.
                        replacement=_unescape_ahk(hotstring_match.group("replacement")),
                        enabled=enabled,
                    )
                )
            pending_source = None

    if not sections:
        sections.append("General")
    store = ExpansionStore(
        sections=sections,
        expansions=expansions,
        variables=variables,
        templates=templates,
    )
    _validate_imported_store(store, path)
    return store


def _validate_imported_store(store: ExpansionStore, path: Path) -> None:
    """Refuse a file that would leave the library unable to generate.

    Deliberately stricter than ExpansionStore.load, because the two failures
    cost different things. Refusing to open the library already on disk locks
    the user out of the application they would use to repair it, so that path
    stays lenient and leaves the complaint to generate time. Refusing an
    import only declines a file: the library is untouched and still works, so
    there is no reason to take on definitions that cannot generate, merge them
    into the live store and autosave them -- which is what happened, with the
    import reported as a success and the failure surfacing later with nothing
    connecting it back.

    References are the one thing not resolved here. A file may legitimately
    use a variable or template the importing library already defines, and
    resolving against the imported file alone would refuse it. Whether the
    merged result resolves is settled at generate time, where the error names
    the trigger.
    """
    try:
        # Names, types, list options, and duplicates within the file.
        validate_variables(store.variables)
        validate_templates(store.templates)
        # Placeholder syntax, which is the same answer wherever the text ends
        # up. resolve_* is deliberately not called: see above.
        for expansion in store.expansions:
            try:
                parse_replacement_template(expansion.replacement)
            except ValueError as exc:
                raise ValueError(
                    f'Invalid placeholder in trigger "{expansion.trigger}": {exc}'
                ) from exc
        for template in store.templates:
            try:
                parse_replacement_template(template.body)
            except ValueError as exc:
                raise ValueError(
                    f'Invalid placeholder in template "{template.name}": {exc}'
                ) from exc
    except ValueError as exc:
        raise ValueError(f"Could not import {path.name}: {exc}") from exc


_BLOCK_OPEN_RE = re.compile(r"^;?\s?\{\s*$")
_BLOCK_CLOSE_RE = re.compile(r"^;?\s?\}\s*$")
_AHK_QUOTED_RE = re.compile(r'"((?:`.|[^"`])*)"')
# A TEM_Select call carrying the branded window title after its options array.
# Scripts generated before the title was added simply do not match.
_SELECT_WINDOW_TITLE_RE = re.compile(r'\],\s*"(?:`.|[^"`])*"\s*\)\s*$')

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


# A form field is one of two shapes discriminated by "kind": only inputs carry a
# default and only selects carry options. Modelling them as a union rather than
# one loose dict means reading the wrong key for the branch is a type error, and
# narrowing on field["kind"] is what makes that check work.
class FormInputField(TypedDict):
    name: str
    kind: Literal["input"]
    label: str
    title: str
    default: str


class FormSelectField(TypedDict):
    name: str
    kind: Literal["select"]
    label: str
    title: str
    options: list[str]


FormField = FormInputField | FormSelectField


def _parse_form_fields(line: str) -> dict[str, FormField] | None:
    """Rebuild the field table from a generated ``__tem_fields`` line.

    Returns None if any entry fails to match, so a block that is not in the
    generated form is refused rather than silently reconstructed short a field.
    """
    fields: dict[str, FormField] = {}
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


def _form_field_placeholder(field: FormField) -> str:
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
    form_fields: dict[str, FormField] = {}
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
        # End-char handling: a fixed 5-line trailer, absent when the expansion
        # ends on a key press.
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
            # Advance one line and let the rules below absorb the rest of the
            # block. A fixed count cannot span both shapes: the block used to
            # be five lines and is four now that the answer is not copied into
            # a local first, and files generated before that are still read.
            i += 1
            continue
        # List selection: 5 lines.
        selection = re.match(r"__tem_select_(\w+) := TEM_Select\(", line)
        if selection:
            quoted = _AHK_QUOTED_RE.findall(line)
            # The window title trails the options array and is derived from the
            # trigger, so it is dropped rather than read back as an option.
            if _SELECT_WINDOW_TITLE_RE.search(line):
                quoted = quoted[:-1]
            if len(quoted) < 2:
                return None
            var = selection.group(1)
            prompt = _unescape_ahk(quoted[0])
            title = _unescape_ahk(quoted[1])
            options = [_unescape_ahk(option) for option in quoted[2:]]
            parts.append(f"{{AHK_SELECT:{var}|{prompt}|{title}|{'||'.join(options)}}}")
            i += 1
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
        # Form field read straight from the values map. This is where the
        # placeholder belongs, and a variable used twice correctly yields two.
        form_read = re.fullmatch(r'__tem_result \.= __tem_vals\["(\w+)"\]', line)
        if form_read:
            if form_read.group(1) not in form_fields:
                return None
            parts.append(_form_field_placeholder(form_fields[form_read.group(1)]))
            i += 1
            continue
        # The tail of an input/select block whose placeholder is already
        # emitted, in the shape that reads the prefixed local directly.
        if re.fullmatch(r"__tem_result \.= __tem_(input|select)_\w+\.\w+", line):
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


def count_import_conflicts(
    target: ExpansionStore, imported: ExpansionStore
) -> ImportConflicts:
    """What the imported store already has counterparts for here.

    A definition matching the one already here is not counted: it needs no
    decision, because skipping and overwriting both leave the library exactly
    as it is. That keeps the question quiet on the common round trip of
    re-importing a file this app generated from this same library, where every
    definition collides by name and none of them differ.

    Renaming still renames those matching definitions -- see
    merge_imported_store -- but only once something else has prompted the
    question.
    """
    return ImportConflicts(
        triggers=sum(
            1
            for expansion in imported.expansions
            if _find_expansion(target, expansion.section, expansion.trigger) is not None
        ),
        definitions=(
            sum(
                1
                for variable in imported.variables
                if _differs(target.variable_by_name(variable.name), variable)
            )
            + sum(
                1
                for template in imported.templates
                if _differs(target.template_by_name(template.name), template)
            )
        ),
    )


def _differs(existing: VariableDef | TemplateDef | None, imported: VariableDef | TemplateDef) -> bool:
    """Whether a same-name definition already here holds something else."""
    if not imported.name or existing is None:
        return False
    return existing.to_dict() != imported.to_dict()


def _renamed_definition(taken: set[str], name: str) -> str:
    """A free name for an imported definition, in the shape triggers use."""
    base = f"{name}_imported"
    candidate = base
    suffix = 2
    while candidate in taken:
        candidate = f"{base}{suffix}"
        suffix += 1
    return candidate


def _definition_renames(
    target: ExpansionStore, imported: ExpansionStore, conflict_action: str
) -> dict[tuple[str, str], str]:
    """New names for the imported definitions that collide, keyed by kind.

    Decided up front, before anything is copied. Renaming a definition without
    also rewriting the references to it would leave the imported expansions
    pointing at the definition already here -- which is the very thing that
    made the collision worth asking about.

    Two things keep the mappings independent of each other. A name is only
    generated for a definition that collides with the target as it stands, so
    a name introduced here can never itself need renaming. And every name in
    use on either side is reserved before any is generated, so a generated
    name cannot land on one that some other definition already answers to --
    which is what turned an imported "v" and "v_imported" into two references
    to the same thing.
    """
    if conflict_action != "rename":
        return {}
    renames: dict[tuple[str, str], str] = {}
    taken = {variable.name for variable in target.variables}
    taken |= {variable.name for variable in imported.variables}
    for variable in imported.variables:
        if variable.name and target.variable_by_name(variable.name) is not None:
            renames["VAR", variable.name] = _renamed_definition(taken, variable.name)
            taken.add(renames["VAR", variable.name])
    taken = {template.name for template in target.templates}
    taken |= {template.name for template in imported.templates}
    for template in imported.templates:
        if template.name and target.template_by_name(template.name) is not None:
            renames["TPL", template.name] = _renamed_definition(taken, template.name)
            taken.add(renames["TPL", template.name])
    return renames


def _merge_definitions(
    target: ExpansionStore,
    imported: ExpansionStore,
    conflict_action: str,
    renames: dict[tuple[str, str], str],
    result: ImportMergeResult,
) -> None:
    """Apply the chosen action to the imported variables and templates.

    Matched by name alone, with no comparison of contents. Whether two
    definitions happen to agree today decides whether the question is worth
    asking, not what the answer does: renaming a definition that currently
    matches still keeps the imported expansions on their own copy, which is
    what "keep both" means and stays true after either copy is edited.
    """
    for imported_variable in imported.variables:
        name = imported_variable.name
        if not name:
            continue
        copy = VariableDef.from_dict(imported_variable.to_dict())
        existing_variable = target.variable_by_name(name)
        if existing_variable is None:
            target.variables.append(copy)
            result.variables_added += 1
        elif conflict_action == "skip":
            result.definitions_skipped += 1
        elif conflict_action == "overwrite":
            existing_variable.type = copy.type
            existing_variable.prompt_text = copy.prompt_text
            existing_variable.default_value = copy.default_value
            existing_variable.list_options = copy.list_options
            existing_variable.notes = copy.notes
            result.definitions_overwritten += 1
        else:
            copy.name = renames["VAR", name]
            target.variables.append(copy)
            result.definitions_renamed += 1

    for imported_template in imported.templates:
        name = imported_template.name
        if not name:
            continue
        copy = TemplateDef.from_dict(imported_template.to_dict())
        # Even a template that is only being added can reference a definition
        # that was renamed, so every body copied across is rewritten.
        copy.body = _apply_renames(copy.body, renames)
        existing_template = target.template_by_name(name)
        if existing_template is None:
            target.templates.append(copy)
            result.templates_added += 1
        elif conflict_action == "skip":
            result.definitions_skipped += 1
        elif conflict_action == "overwrite":
            existing_template.description = copy.description
            existing_template.body = copy.body
            existing_template.notes = copy.notes
            result.definitions_overwritten += 1
        else:
            copy.name = renames["TPL", name]
            target.templates.append(copy)
            result.definitions_renamed += 1


def copy_store(store: ExpansionStore) -> ExpansionStore:
    """An independent copy, sharing no records with the original.

    Lets a merge be carried out and inspected before anything is committed to
    the library the window is showing.
    """
    return ExpansionStore(
        sections=list(store.sections),
        expansions=[Expansion.from_dict(item.to_dict()) for item in store.expansions],
        variables=[VariableDef.from_dict(item.to_dict()) for item in store.variables],
        templates=[TemplateDef.from_dict(item.to_dict()) for item in store.templates],
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

    # Definitions are settled first. A rename among them rewrites the
    # references in the imported text, and that has to happen before any of it
    # is copied across.
    renames = _definition_renames(target, imported, conflict_action)
    _merge_definitions(target, imported, conflict_action, renames, result)

    for imported_expansion in imported.expansions:
        existing = _find_expansion(target, imported_expansion.section, imported_expansion.trigger)
        expansion = Expansion.from_dict(imported_expansion.to_dict())
        expansion.replacement = _apply_renames(expansion.replacement, renames)
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


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text so that a failure part-way cannot truncate the existing file.

    The content lands in a temporary file alongside the target -- the same
    directory, so the closing rename stays on one volume and is atomic -- and is
    flushed to the platform before that rename. A crash, a full disk or a
    process kill therefore leaves either the previous file or the complete new
    one, never a half-written mix of both.

    Newline handling is left at the default so the line endings match what
    Path.write_text produced before this existed.
    """
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except OSError:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def backup_file(path: Path, backup_dir: Path | None = None) -> Path | None:
    """Copy a file aside, keeping the newest BACKUP_RETENTION_LIMIT copies.

    Backups go in backup_dir when given, otherwise beside the file. Returns the
    backup's path, or None when there was nothing to copy.
    """
    if not path.exists():
        return None
    _backup_dir(path, backup_dir).mkdir(parents=True, exist_ok=True)
    backup_path = _backup_path(path, backup_dir)
    shutil.copy2(path, backup_path)
    _cleanup_old_backups(path, backup_dir=backup_dir)
    return backup_path


def migrate_backups(path: Path, source_dir: Path, target_dir: Path) -> int:
    """Move backups of path from one folder to another, returning how many.

    Only files matching the backup naming pattern are touched, and they are
    moved rather than copied, so the folder being emptied is genuinely tidied
    and nothing is duplicated. A name already taken in the target is left
    alone rather than overwritten.
    """
    if source_dir.resolve() == target_dir.resolve():
        return 0
    existing = _app_backup_paths(path, source_dir)
    if not existing:
        return 0
    target_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    for candidate in existing:
        destination = target_dir / candidate.name
        if destination.exists():
            continue
        shutil.move(str(candidate), str(destination))
        moved += 1
    if moved:
        _cleanup_old_backups(path, backup_dir=target_dir)
    return moved


def list_backups(path: Path, backup_dir: Path | None = None) -> list[Path]:
    """Existing backups of a file, newest first."""
    backups = _app_backup_paths(path, backup_dir)
    backups.sort(key=_backup_sort_key, reverse=True)
    return backups


def backup_timestamp(path: Path) -> str:
    """The time a backup was taken, read back out of its filename."""
    match = re.search(r"\.(\d{8})_(\d{6})(?:_(\d+))?\.bak", path.name)
    if not match:
        return path.name
    try:
        taken = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return path.name
    label = taken.strftime("%Y-%m-%d %H:%M:%S")
    # Several backups within the same second are numbered rather than lost.
    if match.group(3):
        label += f" (#{match.group(3)})"
    return label


def restore_backup(
    backup_path: Path, target_path: Path, backup_dir: Path | None = None
) -> Path | None:
    """Replace a file with one of its backups.

    The file being replaced is itself backed up first, so restoring the wrong
    copy is recoverable rather than the end of the current data. Returns that
    safety copy's path, or None when there was no file to replace.
    """
    if not backup_path.exists():
        raise ValueError(f"{backup_path.name} no longer exists.")
    safety_copy = backup_file(target_path, backup_dir)
    shutil.copy2(backup_path, target_path)
    return safety_copy


def generate_ahk(
    store: ExpansionStore,
    path: Path,
    backup: bool = True,
    backup_dir: Path | None = None,
    theme: str = DEFAULT_THEME,
    icon_source: Path | None = None,
) -> Path | None:
    validate_store_placeholders(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = backup_file(path, backup_dir) if backup else None
    # Atomic too: a truncated .ahk would break every hotstring at once, and the
    # running script is reloaded from it immediately after this returns.
    _atomic_write_text(path, render_ahk(store, theme))
    _install_prompt_icon(path, icon_source)
    return backup_path


def _install_prompt_icon(script_path: Path, icon_source: Path | None) -> None:
    """Place the app icon beside the generated script for the prompts to load.

    Best effort: the script guards on the file existing, so a failed copy costs
    the branding but never the expansions.
    """
    if icon_source is None:
        return
    try:
        if not icon_source.is_file():
            return
        target = script_path.parent / AHK_ICON_NAME
        if target.exists() and target.stat().st_mtime >= icon_source.stat().st_mtime:
            return
        shutil.copyfile(icon_source, target)
    except OSError:
        return


def render_ahk(store: ExpansionStore, theme: str = DEFAULT_THEME) -> str:
    validate_store_placeholders(store)
    lines = [
        "#Requires AutoHotkey v2.0",
        "#SingleInstance Force",
        "; Generated by AutoHotkey Text Expansion Manager.",
        "; Edit expansions.json through the GUI, then regenerate this file.",
        "",
        f'if FileExist(A_ScriptDir "\\{AHK_ICON_NAME}")',
        f'    TraySetIcon(A_ScriptDir "\\{AHK_ICON_NAME}")',
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

    colors = ahk_theme_colors(theme)
    # WS_EX_CLIENTEDGE, the sunken frame around an Edit, is drawn in light
    # colours whatever visual style the control carries -- neither
    # DarkMode_Explorer nor DarkMode_CFD reaches it -- so a dark field was
    # ringed in white. Dropping it leaves a flat field that reads against the
    # window by its fill. Light keeps the frame: it looks right there.
    edit_border = "-E0x200 " if theme == "dark" else ""
    # Both prompts position themselves and take their chrome from the same
    # helpers, so the two dialogs cannot drift apart visually.
    if needs_form_helper or needs_select_helper:
        lines.extend(_position_helper_lines())
        lines.append("")
        lines.extend(_chrome_helper_lines(theme))
        lines.append("")
    if needs_form_helper:
        lines.extend(_form_helper_lines(colors, edit_border))
        lines.append("")
    if needs_select_helper:
        lines.extend(_select_helper_lines(colors))
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


def _form_fields(segments: list[str | TemplatePlaceholder]) -> list[FormField]:
    """Collect the prompted placeholders into one ordered list of form fields.

    A variable used more than once yields a single field -- the first occurrence
    defines its prompt, default and options -- so the form asks once and every
    occurrence receives the same answer.
    """
    fields: list[FormField] = []
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


def _use_form(fields: list[FormField]) -> bool:
    """Whether to gather these fields in one form dialog.

    An expansion whose only prompt is a single dropdown keeps the lighter
    TEM_Select popup; a whole form would be overkill for one pick. Everything
    else -- any text input, or more than one prompt -- gets the form, where the
    live preview supplies the context a bare prompt cannot.
    """
    if not fields:
        return False
    return not (len(fields) == 1 and fields[0]["kind"] == "select")


def _form_fields_literal(fields: list[FormField]) -> str:
    """Emit the fields array TEM_Form builds its controls from.

    The title is emitted even though the dialog titles itself with the trigger
    and never draws it: it is the one placeholder argument the generated code
    would otherwise lose, and _reconstruct_replacement needs it to rebuild the
    template from a file whose @tem markers have been stripped.

    Key order is fixed because the reconstruction regexes match on it.
    """
    items: list[str] = []
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


def _form_parts_literal(segments: list[str | TemplatePlaceholder]) -> str:
    """Emit the parts array TEM_Form assembles its live preview from.

    Literals become strings and prompted placeholders become {var} references
    the preview substitutes as the user types. AHK_EXPR is emitted unquoted so
    it evaluates when the array is built -- the user sees the real date rather
    than the expression. Key presses and images have no text form, so they show
    as a bracketed chip standing in for the action.
    """
    items: list[str] = []
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
        return _skipped(expansion, "empty replacement.")

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
        # Every segment is literal on this branch, but expansion.replacement is
        # still the unresolved source. Emitting it verbatim sends the raw
        # {TPL:Name} text for any template whose body holds no placeholder of
        # its own -- so templates appeared to work only while something in them
        # stayed dynamic. The source marker keeps the unresolved text for
        # import; the hotstring gets the resolved text.
        resolved = "".join(cast(str, segment) for segment in segments)
        if resolved == "":
            # As above: "::" with nothing after it is read as an execute
            # hotstring and fails to load. Reachable through a template whose
            # body is empty, which the check at the top of the function cannot
            # see -- and an empty body is easy to arrive at, since a template
            # can be created before it is written.
            return _skipped(expansion, "replacement resolves to nothing.")
        # A static hotstring's replacement runs to the end of its own line, so
        # a line break cannot survive there -- _single_line_replacement folds
        # each one to a space. Replacement text is written in a multiline box,
        # so that silently rewrote anything typed across two lines, and only
        # when the text happened to hold no placeholder: the same paragraph
        # kept its breaks as soon as a variable was added to it. Text with
        # breaks takes the block form instead, where _ahk_string encodes them.
        if use_paste or _is_multiline(resolved):
            send = "TEM_Paste" if use_paste else "SendText"
            lines = [f":{HOTSTRING_OPTIONS}:{expansion.trigger}::", "{"]
            lines.append(f"    {send}({_ahk_string(resolved)})")
            lines.extend(_end_char_lines())
            lines.append("}")
        else:
            lines = [
                f":{STATIC_HOTSTRING_OPTIONS}:{expansion.trigger}::{_single_line_replacement(resolved)}"
            ]
        if expansion.notes:
            lines.extend(_notes_lines(expansion.notes))
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
            f"    __tem_vals := TEM_Form({_ahk_string(_prompt_title(expansion.trigger))}, __tem_fields, __tem_parts)"
        )
        lines.append("    if (!IsObject(__tem_vals))")
        lines.append("        return")
        # No "<name> := __tem_vals[...]" line per field any more. Copying the
        # answers into locals named by the user put user-chosen text into
        # identifier position, where it collided with whatever already had that
        # name: the generator's own locals, AutoHotkey's built-ins, and the
        # functions the block goes on to call. Read from the map instead --
        # the answers are keyed by name and the keys are case-sensitive, which
        # also keeps "Client" and "client" apart where two locals could not be.

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
            # The form gathered every answer up front, including for a repeat
            # occurrence, so each occurrence only reads its key back.
            variable, prompt, title, default = segment.args
            if use_form:
                lines.append(f"    __tem_result .= __tem_vals[{_ahk_string(variable)}]")
            else:
                # Unreachable as it stands: _use_form takes the form for any
                # text input, so only a lone dropdown gets here. Kept in the
                # same shape as the branch below so it cannot become a trap if
                # that rule is ever relaxed.
                lines.append(f"    __tem_input_{variable} := InputBox({_ahk_string(prompt)}, {_ahk_string(title)}, , {_ahk_string(default)})")
                lines.append(f"    if (__tem_input_{variable}.Result = \"Cancel\")")
                lines.append("        return")
                lines.append(f"    __tem_result .= __tem_input_{variable}.Value")
        elif segment.kind == "AHK_SELECT":
            variable, prompt, title, *options = segment.args
            if use_form:
                lines.append(f"    __tem_result .= __tem_vals[{_ahk_string(variable)}]")
            else:
                option_list = ", ".join(_ahk_string(option) for option in options)
                window_title = _ahk_string(_prompt_title(expansion.trigger))
                lines.append(
                    f"    __tem_select_{variable} := TEM_Select({_ahk_string(prompt)}, "
                    f"{_ahk_string(title)}, [{option_list}], {window_title})"
                )
                lines.append(f"    if (!__tem_select_{variable}.ok)")
                lines.append("        return")
                # Read off the prefixed local rather than copying it to one
                # named by the user, for the reason above.
                lines.append(f"    __tem_result .= __tem_select_{variable}.value")
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
    if not _ends_with_key(segments):
        lines.extend(_end_char_lines())
    lines.append("}")
    if expansion.notes:
        lines.extend(_notes_lines(expansion.notes))
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


# Keys double as the labels in the rendered summary, so they carry spaces and a
# slash -- hence the functional TypedDict syntax rather than the class form.
PlaceholderSummary = TypedDict(
    "PlaceholderSummary",
    {
        "Variables": list[str],
        "Date/Time": int,
        "Input boxes": int,
        "List selections": int,
        "Keystrokes": list[str],
        "Images": list[str],
        "Nested templates": list[str],
    },
)


def collect_placeholder_summary(
    segments: list[str | TemplatePlaceholder],
    store: ExpansionStore | None = None,
    stack: tuple[str, ...] = (),
) -> str:
    found: PlaceholderSummary = {
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
    found: PlaceholderSummary,
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


def placeholder_problems(store: ExpansionStore) -> dict[str, str]:
    """Everything stopping this store generating, keyed by what it belongs to.

    All of them, not the first: comparing two stores by whether each has "a
    problem" cannot tell a fault the import introduced from one that was
    already there, and collapses to "already broken, allow anything" as soon
    as the library has a single fault of its own.

    The keys identify a record rather than a position, so they survive
    everything moving around them. Both the key and the message are compared,
    so a record that was already broken and is now broken differently counts
    as a new fault.
    """
    problems: dict[str, str] = {}
    try:
        validate_variables(store.variables)
    except ValueError as exc:
        problems["variables"] = str(exc)
    try:
        validate_templates(store.templates)
    except ValueError as exc:
        problems["templates"] = str(exc)
    for expansion in store.expansions:
        try:
            segments = resolve_template_segments(
                parse_replacement_template(expansion.replacement),
                store.templates,
            )
            resolve_variable_segments(segments, store.variables)
        except ValueError as exc:
            problems[f"expansion {expansion.section}\0{expansion.trigger}"] = (
                f'Invalid placeholder in trigger "{expansion.trigger}": {exc}'
            )
    for template in store.templates:
        try:
            segments = resolve_template_segments(
                parse_replacement_template(template.body),
                store.templates,
                stack=(template.name,),
            )
            resolve_variable_segments(segments, store.variables)
        except ValueError as exc:
            problems[f"template {template.name}"] = (
                f'Invalid placeholder in template "{template.name}": {exc}'
            )
    return problems


def validate_store_placeholders(store: ExpansionStore) -> None:
    """Raise on the first thing stopping this store generating.

    The collector above decides the order, which is the order this function
    used to check in: definitions, then expansions, then template bodies.
    """
    for message in placeholder_problems(store).values():
        raise ValueError(message)


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
    # A reference is written {TPL:name}, and the placeholder pattern reads the
    # name as everything up to the next brace. So a name holding one cannot be
    # referred to: "Bad}Name" yields "{TPL:Bad}Name}", which parses as a
    # reference to a template called "Bad" followed by the text "Name}", and
    # "Bad{Name" does not parse at all. Refused here rather than at generate
    # time, because a rename cascades the broken reference through every
    # expansion that used the old name first.
    #
    # Only braces. Pipes, colons, quotes, semicolons and the rest were each
    # checked and round-trip exactly, so there is nothing to gain by refusing
    # them.
    if "{" in template.name or "}" in template.name:
        raise ValueError(
            f'Template name "{template.name}" cannot contain "{{" or "}}": '
            "a template is referred to as {TPL:name}, so a name holding a "
            "brace cannot be written as a reference."
        )


# Which library item a reference points at: a variable or another template.
ReferenceKind = Literal["VAR", "TPL"]


def _text_references(text: str, kind: ReferenceKind, name: str) -> bool:
    """Whether text uses {VAR:name} or {TPL:name}.

    Matched with the placeholder pattern rather than parsed, because the
    callers ask this while deciding whether a rename or a delete is safe --
    exactly when a library is most likely to be half-broken elsewhere, and a
    parse error somewhere unrelated must not hide a real reference here.
    """
    return any(
        match.group(1) == kind and match.group(2).strip() == name
        for match in PLACEHOLDER_RE.finditer(text)
    )


def find_references(store: ExpansionStore, kind: ReferenceKind, name: str) -> list[str]:
    """Everything in the library that uses this variable or template.

    Labelled for a dialog rather than returned as objects: the callers only
    need to tell the user what would break.
    """
    labels = [
        f'expansion "{expansion.trigger}"'
        for expansion in store.expansions
        if _text_references(expansion.replacement, kind, name)
    ]
    labels += [
        f'template "{template.name}"'
        for template in store.templates
        if _text_references(template.body, kind, name)
    ]
    return labels


def _apply_renames(text: str, renames: dict[tuple[str, str], str]) -> str:
    """Point every renamed reference in text at its new name, in one pass.

    One pass rather than one call per mapping: applied in turn, an earlier
    substitution's output is still there to be matched by a later one, so a
    mapping onto a name that another mapping renames would carry the first
    reference along with it.

    Substituted over the matched spans so everything else in the text -- other
    placeholders, spacing, the surrounding prose -- is returned byte for byte.
    Rebuilding the string from parsed segments would reformat placeholders the
    user never touched.
    """
    if not renames:
        return text

    def swap(match: re.Match[str]) -> str:
        # Keyed on what the text says, never on what a substitution produced.
        new = renames.get((match.group(1), match.group(2).strip()))
        return match.group(0) if new is None else f"{{{match.group(1)}:{new}}}"

    return PLACEHOLDER_RE.sub(swap, text)


def rename_in_text(text: str, kind: ReferenceKind, old: str, new: str) -> str:
    """Point every {VAR:old} / {TPL:old} in text at new."""
    return _apply_renames(text, {(kind, old): new})


def rename_references(
    store: ExpansionStore, kind: ReferenceKind, old: str, new: str
) -> int:
    """Point the whole library at the new name. Returns how many texts changed.

    Renaming a variable or template used to leave every reference to it
    dangling: the library still autosaved, and only generation failed, well
    after the rename that caused it.
    """
    changed = 0
    for expansion in store.expansions:
        updated = rename_in_text(expansion.replacement, kind, old, new)
        if updated != expansion.replacement:
            expansion.replacement = updated
            changed += 1
    for template in store.templates:
        updated = rename_in_text(template.body, kind, old, new)
        if updated != template.body:
            template.body = updated
            changed += 1
    return changed


def _validate_unmatched_placeholders(text: str, matched_starts: set[int]) -> None:
    for match in PLACEHOLDER_START_RE.finditer(text):
        if match.start() in matched_starts:
            continue
        closing = text.find("}", match.start())
        if closing == -1:
            raise ValueError("Placeholder is missing a closing brace.")
        raise ValueError("Placeholder syntax is malformed or contains unsupported braces.")


def _validate_variable_name(value: str, placeholder_name: str) -> None:
    # No reserved-name rules any more. They existed because the answer was
    # copied into a local named by the user, which collided with the
    # generator's own locals, AutoHotkey's built-ins and its keywords. Answers
    # are read out of a map now, so the name reaches the script only as a
    # string key -- and a list of names that no longer break anything would
    # just be refusing valid input.
    # "<caller> name" rather than "<caller> variable": the caller for a saved
    # definition is "Variable", which read as "Variable variable must be...".
    if not VARIABLE_RE.match(value):
        raise ValueError(
            f'{placeholder_name} name "{value}" must be a valid AutoHotkey identifier.'
        )


def _skipped_marker(expansion: Expansion) -> str:
    """The whole record for an expansion that generates no hotstring.

    _source_marker carries only what the hotstring line cannot -- the trigger,
    the enabled state and the section come from the line and the header. A
    skipped expansion emits neither, so nothing tied the marker to an
    expansion and a re-import dropped it. This carries the full record, as the
    variable and template markers already do.
    """
    return "; @tem-skipped: " + json.dumps(
        expansion.to_dict(), ensure_ascii=False, separators=(",", ":")
    )


def _skipped(expansion: Expansion, reason: str) -> RenderedExpansion:
    """An expansion that emits no hotstring, plus the record to get it back.

    The comment is for whoever reads the generated file; the marker above it is
    what import reads.
    """
    return RenderedExpansion(
        [
            _skipped_marker(expansion),
            f'; Skipped "{expansion.trigger}": {reason}',
        ]
    )


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


def _notes_lines(notes: str) -> list[str]:
    """The human-readable notes comment, one comment marker per physical line.

    Notes are written in a multiline box, so the value arrives with the line
    breaks the user typed. Emitted as a single "; Notes: <value>" string, only
    the first physical line is commented -- every line after it is written to
    the script at column zero, in code position, where AutoHotkey parses it. A
    stray brace or an unbalanced bracket fails the load outright; a line that
    happens to be valid syntax runs, and a line that happens to look like a
    hotstring silently defines a second one.

    Returning a line per element rather than one embedded-newline string also
    covers the disabled case, since _maybe_disable_lines prefixes each element
    and cannot see inside one.

    The label repeats on every line instead of indenting the continuations to
    align under it, which would read better but is not safe: this file is also
    parsed by import_ahk, and a comment marker followed by only whitespace is
    exactly the prefix HOTSTRING_RE accepts for a disabled hotstring and
    SECTION_RE for a section header. An aligned note line beginning ":CT:..."
    would come back as a real (disabled) expansion and one reading "=== x ==="
    would open a new section. "Notes: " is non-blank, so neither pattern can
    reach past it.

    The source marker above carries the notes as JSON on a single line and is
    what import reads back, so this comment exists purely for someone reading
    the generated file.
    """
    return [f"; Notes: {line}" for line in notes.splitlines() or [""]]


def _maybe_disable_lines(lines: list[str], enabled: bool) -> list[str]:
    if enabled:
        return lines
    return [f"; {line}" if line else ";" for line in lines]


def _ahk_string(value: str) -> str:
    # A semicolon preceded by whitespace opens a comment even inside a quoted
    # string, which truncates the literal and fails to parse. Triggers are
    # conventionally written ";abc", so escape every semicolon: `; is a literal
    # semicolon to AHK, and _unescape_ahk already reverses an unknown escape to
    # the character itself.
    escaped = (
        value.replace("`", "``")
        .replace("\r\n", "`r`n")
        .replace("\n", "`n")
        .replace("\r", "`r")
        .replace('"', '`"')
        .replace(";", "`;")
    )
    return f'"{escaped}"'


def _select_helper_lines(colors: dict[str, str]) -> list[str]:
    # winTitle is optional so a hand-edited script written against the older
    # three-argument form still runs, falling back to the placeholder's title.
    return [
        "TEM_Select(prompt, title, options, winTitle := \"\") {",
        "    point := TEM_TargetPoint()",
        "    selectGui := Gui(\"+AlwaysOnTop\", winTitle != \"\" ? winTitle : title)",
        f"    selectGui.BackColor := \"{colors['bg']}\"",
        "    TEM_ApplyChrome(selectGui.Hwnd)",
        f"    selectGui.SetFont(\"s9 c{colors['text']}\", \"Segoe UI\")",
        "    selectGui.AddText(\"w280\", prompt)",
        "    dropdown := selectGui.AddDropDownList(\"w280 Choose1\", options)",
        "    TEM_ThemeControl(dropdown.Hwnd, \"DarkMode_CFD\")",
        "    result := {ok: false, value: \"\"}",
        "    okButton := selectGui.AddButton(\"Default w80\", \"OK\")",
        "    cancelButton := selectGui.AddButton(\"x+8 w80\", \"Cancel\")",
        "    TEM_ThemeControl(okButton.Hwnd, \"DarkMode_Explorer\")",
        "    TEM_ThemeControl(cancelButton.Hwnd, \"DarkMode_Explorer\")",
        "    okButton.OnEvent(\"Click\", (*) => (result.ok := true, result.value := dropdown.Text, selectGui.Destroy()))",
        "    cancelButton.OnEvent(\"Click\", (*) => selectGui.Destroy())",
        "    selectGui.OnEvent(\"Close\", (*) => selectGui.Destroy())",
        "    TEM_ShowAt(selectGui, point)",
        "    guiHwnd := selectGui.Hwnd",
        "    WinWaitClose(\"ahk_id \" guiHwnd)",
        "    return result",
        "}",
    ]


def _chrome_helper_lines(theme: str) -> list[str]:
    """Give a prompt window the app icon and, in dark mode, a dark title bar.

    Both are best effort by design: a Windows build without the immersive dark
    mode attribute ignores the call, and a missing icon file leaves the default
    AutoHotkey one. Neither should cost the user their expansion.
    """
    dark_flag = "1" if theme == "dark" else "0"
    # Buttons, dropdowns and scrollbars are drawn by Windows, which ignores the
    # Gui's colours entirely, so the dark variants of their visual styles are
    # the only way to bring them along. Light needs nothing: the defaults
    # already match, and applying a DarkMode style there would invert them.
    theme_control_body = (
        ["    try DllCall(\"uxtheme\\SetWindowTheme\", \"ptr\", hwnd, \"str\", sub, \"ptr\", 0)"]
        if theme == "dark"
        else ["    ; Light theme keeps the default visual styles, which match."]
    )
    return [
        "TEM_ApplyChrome(hwnd) {",
        f"    TEM_DarkTitleBar(hwnd, {dark_flag})",
        "    TEM_SetWindowIcon(hwnd)",
        "}",
        "",
        "TEM_ThemeControl(hwnd, sub) {",
        *theme_control_body,
        "}",
        "",
        "TEM_DarkTitleBar(hwnd, enable) {",
        "    ; DWMWA_USE_IMMERSIVE_DARK_MODE (20). Older builds fail harmlessly.",
        "    try DllCall(\"dwmapi\\DwmSetWindowAttribute\", \"ptr\", hwnd, \"int\", 20, \"int*\", enable, \"int\", 4)",
        "}",
        "",
        "TEM_SetWindowIcon(hwnd) {",
        f"    iconPath := A_ScriptDir \"\\{AHK_ICON_NAME}\"",
        "    if !FileExist(iconPath)",
        "        return",
        "    ; IMAGE_ICON with LR_LOADFROMFILE, then set both sizes so the title",
        "    ; bar and Alt-Tab agree.",
        "    hIcon := DllCall(\"LoadImage\", \"ptr\", 0, \"str\", iconPath, \"uint\", 1, \"int\", 0, \"int\", 0, \"uint\", 0x10, \"ptr\")",
        "    if !hIcon",
        "        return",
        "    ; Sent through DllCall rather than SendMessage: the window is still",
        "    ; hidden at this point, so an ahk_id criterion would not match it",
        "    ; unless DetectHiddenWindows were on.",
        "    DllCall(\"SendMessage\", \"ptr\", hwnd, \"uint\", 0x0080, \"ptr\", 0, \"ptr\", hIcon)",
        "    DllCall(\"SendMessage\", \"ptr\", hwnd, \"uint\", 0x0080, \"ptr\", 1, \"ptr\", hIcon)",
        "}",
    ]


def _position_helper_lines() -> list[str]:
    """Place a prompt on the monitor the trigger was typed on.

    Showing a dialog with no coordinates lands it on whichever monitor Windows
    picks, which on a multi-monitor desk is routinely not the one the user is
    typing on. The anchor is taken before the dialog exists, because showing it
    makes the script the active window and loses the target.

    The caret is the most accurate anchor but is unavailable in a fair number of
    apps -- Chrome and most Electron shells among them -- so the active window's
    centre and finally the mouse stand in for it.
    """
    return [
        "TEM_TargetPoint() {",
        # Both default to Client, which would report the caret relative to the
        # typed-in window and pick the wrong monitor. Restored so the calling
        # hotstring is not left with modes it did not set.
        "    priorCaret := A_CoordModeCaret",
        "    priorMouse := A_CoordModeMouse",
        "    CoordMode(\"Caret\", \"Screen\")",
        "    CoordMode(\"Mouse\", \"Screen\")",
        "    try {",
        "        if (CaretGetPos(&caretX, &caretY) && (caretX || caretY))",
        "            return [caretX, caretY]",
        "        if (targetHwnd := WinExist(\"A\")) {",
        "            WinGetPos(&winX, &winY, &winW, &winH, targetHwnd)",
        "            return [winX + winW // 2, winY + winH // 2]",
        "        }",
        "        MouseGetPos(&mouseX, &mouseY)",
        "        return [mouseX, mouseY]",
        "    } finally {",
        "        CoordMode(\"Caret\", priorCaret)",
        "        CoordMode(\"Mouse\", priorMouse)",
        "    }",
        "}",
        "",
        "TEM_MonitorAt(x, y) {",
        "    loop MonitorGetCount() {",
        "        MonitorGetWorkArea(A_Index, &left, &top, &right, &bottom)",
        "        if (x >= left && x < right && y >= top && y < bottom)",
        "            return A_Index",
        "    }",
        "    return MonitorGetPrimary()",
        "}",
        "",
        "TEM_ShowAt(targetGui, point) {",
        "    MonitorGetWorkArea(TEM_MonitorAt(point[1], point[2]), &left, &top, &right, &bottom)",
        "    targetGui.Show(\"Hide\")",
        "    targetGui.GetPos(, , &guiW, &guiH)",
        "    x := Max(left, left + ((right - left) - guiW) // 2)",
        "    y := Max(top, top + ((bottom - top) - guiH) // 2)",
        "    targetGui.Show(\"x\" x \" y\" y)",
        "}",
    ]


def _form_helper_lines(colors: dict[str, str], edit_border: str = "") -> list[str]:
    """One dialog gathering every prompt, above a preview of the resolved text.

    updatePreview reads the live controls on each keystroke rather than closing
    over per-field state, which also sidesteps the v2 closure-in-a-loop trap
    where every handler would otherwise capture the last field.
    """
    return [
        "TEM_Form(title, fields, parts) {",
        "    point := TEM_TargetPoint()",
        "    formGui := Gui(\"+AlwaysOnTop +OwnDialogs\", title)",
        f"    formGui.BackColor := \"{colors['bg']}\"",
        "    TEM_ApplyChrome(formGui.Hwnd)",
        f"    formGui.SetFont(\"s9 c{colors['text']}\", \"Segoe UI\")",
        "    formGui.AddText(\"xm w460\", \"Preview\")",
        f"    preview := formGui.AddEdit(\"xm w460 r4 Multi ReadOnly {edit_border}Background{colors['field']}\")",
        # DarkMode_Explorer rather than DarkMode_CFD: with the frame gone the
        # only thing left for the style to reach is the scrollbar, which CFD
        # leaves light.
        "    TEM_ThemeControl(preview.Hwnd, \"DarkMode_Explorer\")",
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
        "        if (field[\"kind\"] = \"select\") {",
        "            ctrl := formGui.AddDropDownList(\"x+8 yp-4 w312 Choose1\", field[\"options\"])",
        "            TEM_ThemeControl(ctrl.Hwnd, \"DarkMode_CFD\")",
        "        } else {",
        f"            ctrl := formGui.AddEdit(\"x+8 yp-4 w312 {edit_border}Background{colors['field']}\", field[\"default\"])",
        "            TEM_ThemeControl(ctrl.Hwnd, \"DarkMode_CFD\")",
        "        }",
        "        ctrl.OnEvent(\"Change\", (*) => updatePreview())",
        "        controls[field[\"name\"]] := ctrl",
        "    }",
        "    okButton := formGui.AddButton(\"xm+272 y+16 w90 Default\", \"Insert\")",
        "    cancelButton := formGui.AddButton(\"x+8 w90\", \"Cancel\")",
        "    TEM_ThemeControl(okButton.Hwnd, \"DarkMode_Explorer\")",
        "    TEM_ThemeControl(cancelButton.Hwnd, \"DarkMode_Explorer\")",
        "    okButton.OnEvent(\"Click\", (*) => (state[\"ok\"] := true, state[\"values\"] := TEM_FormValues(fields, controls), formGui.Destroy()))",
        "    cancelButton.OnEvent(\"Click\", (*) => formGui.Destroy())",
        "    formGui.OnEvent(\"Close\", (*) => formGui.Destroy())",
        "    formGui.OnEvent(\"Escape\", (*) => formGui.Destroy())",
        "    updatePreview()",
        "    TEM_ShowAt(formGui, point)",
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


def _ends_with_key(segments: list[Any]) -> bool:
    """Whether the expansion finishes on a key press.

    Trailing empty literals are ignored: they contribute nothing to the output,
    so a key before one still ends the expansion.
    """
    for segment in reversed(segments):
        if isinstance(segment, str):
            if segment:
                return False
            continue
        return segment.kind == "AHK_KEY"
    return False


def _end_char_lines() -> list[str]:
    # Dynamic hotstrings run code instead of auto-replacing, so AutoHotkey does
    # not reproduce the ending character (space/Enter/Tab) that triggered them.
    # Re-send A_EndChar so it is preserved, matching plain-text hotstrings.
    #
    # Omitted for an expansion that ends on a key press: the caret has moved on
    # by then -- a trailing Tab lands it in the next field -- so replaying the
    # character would type it somewhere the user did not expand into.
    return [
        '    if (A_EndChar = "`r" || A_EndChar = "`n") {',
        '        Send("{Enter}")',
        '    } else if (A_EndChar != "") {',
        "        SendText(A_EndChar)",
        "    }",
    ]


def _is_multiline(text: str) -> bool:
    return "\n" in text or "\r" in text


def _single_line_replacement(text: str) -> str:
    # render_expansion routes text with line breaks to the block form, so the
    # collapse below should no longer have anything to do. It stays as a guard
    # rather than an assertion because the failure it prevents is the worse
    # one: a raw newline here would end the hotstring and leave the rest of the
    # replacement sitting in code position, where AutoHotkey tries to run it.
    collapsed = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    # A static replacement runs to the end of the line, where AHK still reads a
    # backtick as its escape character and opens a comment at a semicolon that
    # follows whitespace -- which silently drops the rest of the expansion
    # rather than failing, so it has to be escaped here. import_ahk reverses
    # this with _unescape_ahk.
    return collapsed.replace("`", "``").replace(";", "`;")


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


def _backup_dir(path: Path, backup_dir: Path | None) -> Path:
    """Where backups of path live: a dedicated folder, or beside the file."""
    return path.parent if backup_dir is None else backup_dir


def _backup_path(path: Path, backup_dir: Path | None = None) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_dir = _backup_dir(path, backup_dir)
    suffixes = _existing_backup_suffixes(path, timestamp, backup_dir)
    if not suffixes:
        return target_dir / f"{path.stem}.{timestamp}.bak{path.suffix}"
    next_suffix = max(suffixes) + 1
    return target_dir / f"{path.stem}.{timestamp}_{next_suffix}.bak{path.suffix}"


def _cleanup_old_backups(
    path: Path,
    keep: int = BACKUP_RETENTION_LIMIT,
    backup_dir: Path | None = None,
) -> None:
    backups = _app_backup_paths(path, backup_dir)
    if len(backups) <= keep:
        return

    backups.sort(key=_backup_sort_key, reverse=True)
    for backup_path in backups[keep:]:
        backup_path.unlink()


def _app_backup_paths(path: Path, backup_dir: Path | None = None) -> list[Path]:
    target_dir = _backup_dir(path, backup_dir)
    if not target_dir.is_dir():
        return []
    escaped_stem = re.escape(path.stem)
    escaped_suffix = re.escape(path.suffix)
    backup_re = re.compile(
        rf"^{escaped_stem}\.\d{{8}}_\d{{6}}(?:_\d+)?\.bak{escaped_suffix}$"
    )
    return [
        candidate
        for candidate in target_dir.iterdir()
        if candidate.is_file() and backup_re.match(candidate.name)
    ]


def _backup_sort_key(path: Path) -> tuple[str, int, str]:
    match = re.search(r"\.(\d{8}_\d{6})(?:_(\d+))?\.bak", path.name)
    if not match:
        return ("", 0, path.name)
    suffix = int(match.group(2) or "1")
    return (match.group(1), suffix, path.name)


def _existing_backup_suffixes(
    path: Path, timestamp: str, backup_dir: Path | None = None
) -> list[int]:
    target_dir = _backup_dir(path, backup_dir)
    if not target_dir.is_dir():
        return []
    escaped_stem = re.escape(path.stem)
    escaped_suffix = re.escape(path.suffix)
    backup_re = re.compile(
        rf"^{escaped_stem}\.{re.escape(timestamp)}(?:_(\d+))?\.bak{escaped_suffix}$"
    )
    suffixes: list[int] = []
    for candidate in target_dir.iterdir():
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
