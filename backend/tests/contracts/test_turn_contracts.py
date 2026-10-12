"""The turn contract corpus (spec section 11). Golden files under
contracts/turns/ pin the ordered frame sequence and the persisted parts per
scenario. Regenerate ON PURPOSE with POSEIDON_UPDATE_CONTRACTS=1 and commit
the diff; an unexplained diff here is a contract change."""

import json
import os
from pathlib import Path

import pytest

from poseidon.core.chat.history import _build_opener_parts
from tests.contracts.scenarios import SCENARIOS, to_json

CORPUS = Path(__file__).resolve().parents[3] / "contracts" / "turns"
UPDATE = os.environ.get("POSEIDON_UPDATE_CONTRACTS") == "1"


def _check_or_write(path: Path, payload: dict) -> None:
    rendered = json.dumps(payload, indent=1, sort_keys=True) + "\n"
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        return
    assert path.exists(), f"{path} missing: run with POSEIDON_UPDATE_CONTRACTS=1"
    assert path.read_text(encoding="utf-8") == rendered, f"{path.name} drifted from the code"


@pytest.mark.parametrize("scenario_id", sorted(SCENARIOS))
def test_scenario_matches_its_golden_file(scenario_id, monkeypatch):
    frames, parts = SCENARIOS[scenario_id](monkeypatch)
    _check_or_write(
        CORPUS / f"{scenario_id}.json", {"scenario": scenario_id, **to_json(frames, parts)}
    )


def test_opener_parts_golden():
    _check_or_write(CORPUS / "opener.json", {"parts": _build_opener_parts()})


def test_every_golden_file_has_a_scenario():
    on_disk = {p.stem for p in CORPUS.glob("*.json")} - {"opener"}
    assert on_disk == set(SCENARIOS)
