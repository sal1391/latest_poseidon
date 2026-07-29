"""Tests for the deterministic offline synthetic-data generator (task 5).

These are the six tests specified verbatim in the task-5 brief. No database,
no network, no wall-clock: ``generate()`` reads only
``ontology/synthetic/profiles.yml`` and a seeded ``random.Random`` instance.
"""

from poseidon.core.ontology.loader import load
from poseidon.scripts.generate_synthetic import dataset_checksum, generate


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
