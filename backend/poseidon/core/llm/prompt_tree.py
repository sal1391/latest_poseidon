"""The versioned prompt tree (decision D46, first step).

D46 moves prompts into one canonical top-level ``prompts/`` tree of XML
system prompts. Ruling 1 of the Phase 17 PR A1 plan puts that tree at
``backend/prompts``, next to the ``poseidon`` package, because ``backend/``
is the runtime root in every habitat (local, compose, EC2, SPCS).

This is deliberately NOT ``PromptRegistry``: that registry owns the older
``core/llm`` prompt fragments, while the tree resolves one id to one XML
file. ``core/llm`` must never import ``poseidon.tasks``; skills call in.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PROMPT_TREE = Path(__file__).resolve().parents[3] / "prompts"


class PromptTreeError(KeyError):
    """A prompt id is unsafe, missing, or its file is malformed."""

    def __str__(self) -> str:  # KeyError would repr() the message
        return str(self.args[0]) if self.args else ""


@dataclass(frozen=True)
class PromptSpec:
    id: str
    version: str
    system: str


class PromptTree:
    def __init__(self, root: Path = DEFAULT_PROMPT_TREE) -> None:
        self._root = root

    def resolve(self, prompt_id: str) -> PromptSpec:
        if ".." in prompt_id or prompt_id.startswith("/"):
            raise PromptTreeError(f"invalid prompt id {prompt_id!r}")
        path = self._root / f"{prompt_id}.xml"
        if not path.is_file():
            raise PromptTreeError(f"unknown prompt id {prompt_id!r}")
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as exc:
            raise PromptTreeError(f"prompt {prompt_id!r} is not valid XML: {exc}") from exc
        if root.tag != "prompt":
            raise PromptTreeError(f"prompt {prompt_id!r}: root tag is {root.tag!r}, not 'prompt'")
        declared = root.get("id")
        if declared != prompt_id:
            raise PromptTreeError(f"prompt file for {prompt_id!r} declares id {declared!r}")
        version = root.get("version")
        if not version:
            raise PromptTreeError(f"prompt {prompt_id!r} has no version attribute")
        system = root.find("system")
        if system is None or system.text is None:
            raise PromptTreeError(f"prompt {prompt_id!r} has no <system> text")
        return PromptSpec(id=prompt_id, version=version, system=system.text.strip())
