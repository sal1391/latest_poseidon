"""Tests for the deterministic offline synthetic-data generator (task 5).

These are the six tests specified verbatim in the task-5 brief. No database,
no network, no wall-clock: ``generate()`` reads only
``ontology/synthetic/profiles.yml`` and a seeded ``random.Random`` instance.
"""

import yaml

from poseidon.core.ontology.loader import load
from poseidon.scripts.generate_synthetic import DEFAULT_PROFILES_PATH, dataset_checksum, generate


def test_same_seed_same_checksum():
    a, b = generate(seed=1391), generate(seed=1391)
    assert dataset_checksum(a) == dataset_checksum(b)


def test_different_seed_differs():
    assert dataset_checksum(generate(seed=1391)) != dataset_checksum(generate(seed=2))


def test_rows_conform_to_ontology():
    ds = generate(seed=1391)
    ont = load()
    assert set(ds.sales_rows[0]) == set(ont.entity("MARINE_SALES_PLANNING_V").columns)
    assert set(ds.gl_rows[0]) == set(ont.entity("W_MARINE_GL_SOURCE_AI").columns)


def test_named_customers_present_with_singapore_activity():
    ds = generate(seed=1391)
    sing = [r for r in ds.sales_rows if r["LOC_NM"] == "Singapore"]
    assert {"Northstar Lines", "Blue Anchor Marine", "Crestline Freight"} <= {
        r["CUST_NM"] for r in sing
    }


def test_inquiry_fixture_semantics():
    ds = generate(seed=1391)
    assert all(r["#_INQUIRIES"] == 1.0 for r in ds.sales_rows)
    lost = [r for r in ds.sales_rows if r["#_FIXTURES"] == 0.0]
    assert lost and all(r["FIXED_TONS"] == 0.0 and r["GROSS_PROFIT"] == 0.0 for r in lost)


def test_gl_volume_rows_exercise_guard():
    ds = generate(seed=1391)
    vol = [r for r in ds.gl_rows if r["CLASS4"] == "Volume"]
    assert vol and all(str(r["ACCOUNT"]).startswith("Tonnage-") for r in vol)
    assert 0.05 < len(vol) / len(ds.gl_rows) < 0.15


_NAMED = ("Northstar Lines", "Blue Anchor Marine", "Crestline Freight")
_OFFICE_COLUMNS = ("CUSTOMER_TEAM_NAME", "PRIMARY_SUPPLY_TEAM_OFFICE")


def _offices_by_customer(rows, column):
    out: dict[str, set] = {}
    for r in rows:
        out.setdefault(r["CUST_NM"], set()).add(r[column])
    return out


def test_multi_office_overlay_gives_named_customers_several_offices():
    ds = generate(seed=1391)
    for column in _OFFICE_COLUMNS:
        offices = _offices_by_customer(ds.sales_rows, column)
        for name in _NAMED:
            assert len(offices[name]) >= 2, (name, column, offices[name])
    team = _offices_by_customer(ds.sales_rows, "CUSTOMER_TEAM_NAME")
    supply = _offices_by_customer(ds.sales_rows, "PRIMARY_SUPPLY_TEAM_OFFICE")
    assert any(len(team[c]) == 1 and len(supply[c]) == 1 for c in team)


def test_overlay_changes_only_the_two_office_columns(tmp_path):
    profile = yaml.safe_load(DEFAULT_PROFILES_PATH.read_text(encoding="utf-8"))
    del profile["MARINE_SALES_PLANNING_V"]["multi_office"]
    plain_path = tmp_path / "profiles.yml"
    plain_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")

    overlaid = generate(seed=1391)
    plain = generate(seed=1391, profiles_path=plain_path)
    assert len(overlaid.sales_rows) == len(plain.sales_rows)
    assert overlaid.gl_rows == plain.gl_rows
    changed = 0
    for a, b in zip(overlaid.sales_rows, plain.sales_rows, strict=True):
        assert {k: v for k, v in a.items() if k not in _OFFICE_COLUMNS} == {
            k: v for k, v in b.items() if k not in _OFFICE_COLUMNS
        }
        changed += any(a[c] != b[c] for c in _OFFICE_COLUMNS)
    assert changed > 0
