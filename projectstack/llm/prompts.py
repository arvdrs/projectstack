"""
Versioned prompt templates, stored as files in projectstack/llm/prompts/:

    <id>.v<version>.md

    ---
    id: report_tech
    version: 1
    description: …
    ---
    <<<SYSTEM>>>
    system prompt …
    <<<USER>>>
    user prompt with {{placeholders}} …

load_prompt("report_tech") picks the highest version; load_prompt("report_tech.v1")
pins one. Placeholders use {{name}} so JSON braces in prompts need no escaping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PROMPT_DIR = Path(__file__).parent / "prompts"
_FILE_RE = re.compile(r"^(?P<id>[a-z0-9_]+)\.v(?P<v>\d+)\.md$")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")


@dataclass
class Prompt:
    id: str
    version: int
    description: str
    system: str
    user: str
    path: Path

    @property
    def ref(self) -> str:
        return f"{self.id}.v{self.version}"

    def render(self, **values: str) -> tuple[str, str]:
        """(system, user) with {{placeholders}} filled. Every placeholder must be given."""
        def fill(text: str) -> str:
            missing = sorted(set(_PLACEHOLDER_RE.findall(text)) - set(values))
            if missing:
                raise KeyError(f"Prompt {self.ref} needs values for: {', '.join(missing)}")
            return _PLACEHOLDER_RE.sub(lambda m: str(values[m.group(1)]), text)
        return fill(self.system), fill(self.user)


def load_prompt(name: str) -> Prompt:
    if ".v" in name:
        path = PROMPT_DIR / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(f"No prompt {name} in {PROMPT_DIR}")
    else:
        versions = [(int(m.group("v")), p) for p in PROMPT_DIR.glob(f"{name}.v*.md")
                    if (m := _FILE_RE.match(p.name)) and m.group("id") == name]
        if not versions:
            raise FileNotFoundError(f"No prompt {name!r} in {PROMPT_DIR}")
        path = max(versions)[1]
    return _parse(path)


def _parse(path: Path) -> Prompt:
    text = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    if text.startswith("---"):
        head, _, text = text[3:].partition("\n---")
        for line in head.strip().splitlines():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
        text = text.lstrip("\n")
    system, sep, user = text.partition("<<<USER>>>")
    if not sep:
        raise ValueError(f"{path.name}: missing <<<USER>>> section")
    system = system.replace("<<<SYSTEM>>>", "", 1).strip()
    m = _FILE_RE.match(path.name)
    return Prompt(meta.get("id") or (m.group("id") if m else path.stem), int(meta.get("version") or (m.group("v") if m else 1)),
                  meta.get("description", ""), system, user.strip(), path)
