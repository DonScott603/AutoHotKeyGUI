import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_JSON = "expansions.json"
DEFAULT_AHK = "text_expansions.ahk"


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


SECTION_RE = re.compile(r"^\s*;\s*=+\s*(?P<section>.*?)\s*=+\s*$")
HOTSTRING_RE = re.compile(r"^\s*(?P<disabled>;\s*)?:(?P<options>[^:]*)?:(?P<trigger>[^:\s][^:]*)::(?P<replacement>.*)$")


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


def generate_ahk(store: ExpansionStore, path: Path, backup: bool = True) -> Path | None:
    if backup and path.exists():
        backup_path = _backup_path(path)
        shutil.copy2(path, backup_path)
    else:
        backup_path = None

    path.write_text(render_ahk(store), encoding="utf-8")
    return backup_path


def render_ahk(store: ExpansionStore) -> str:
    lines = [
        "#Requires AutoHotkey v2.0",
        "; Generated by AutoHotkey Text Expansion Manager.",
        "; Edit expansions.json through the GUI, then regenerate this file.",
        "",
    ]

    for section in store.sections:
        section_expansions = [item for item in store.expansions if item.section == section]
        lines.append(f"; === {section} ===")
        if not section_expansions:
            lines.append("; No expansions in this section.")
        for expansion in section_expansions:
            line = f"::{expansion.trigger}::{_single_line_replacement(expansion.replacement)}"
            if expansion.enabled:
                lines.append(line)
            else:
                lines.append(f"; {line}")
            if expansion.notes:
                lines.append(f"; Notes: {expansion.notes}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _single_line_replacement(text: str) -> str:
    return text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def _backup_path(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return path.with_name(f"{path.stem}.{timestamp}.bak{path.suffix}")
