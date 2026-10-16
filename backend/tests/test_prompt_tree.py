from pathlib import Path

import pytest

from poseidon.core.llm.prompt_tree import (
    DEFAULT_PROMPT_TREE,
    PromptSpec,
    PromptTree,
    PromptTreeError,
)


def test_default_tree_is_backend_prompts():
    assert DEFAULT_PROMPT_TREE.name == "prompts"
    assert DEFAULT_PROMPT_TREE.parent.name == "backend"
    assert (DEFAULT_PROMPT_TREE / "reporting" / "narrate.xml").is_file()


def test_resolve_narrate_returns_version_and_system_text():
    spec = PromptTree().resolve("reporting/narrate")
    assert spec == PromptSpec(id="reporting/narrate", version="2", system=spec.system)
    assert "marginals" in spec.system
    assert "Respond only by calling the narrate tool." in spec.system
    assert spec.system == spec.system.strip()
    assert "{entity_label}" not in spec.system
    assert "Account context" in spec.system
    assert "Insights" not in spec.system.split("Account context")[1].split("Return three")[0]
    assert "insights field" in spec.system


def test_unknown_id_raises():
    with pytest.raises(PromptTreeError, match="reporting/missing"):
        PromptTree().resolve("reporting/missing")


def test_id_mismatch_raises(tmp_path: Path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "y.xml").write_text(
        '<prompt id="x/other" version="3"><system>hi</system></prompt>', encoding="utf-8"
    )
    with pytest.raises(PromptTreeError, match="x/other"):
        PromptTree(tmp_path).resolve("x/y")


def test_ids_cannot_escape_the_tree():
    with pytest.raises(PromptTreeError):
        PromptTree().resolve("../pyproject")
