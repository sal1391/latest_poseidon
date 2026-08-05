from pathlib import Path

import pydantic
import pytest

from poseidon.core.ontology.loader import load


def test_inventory_is_pinned():
    ont = load()
    assert ont.version == 2
    assert sorted(ont.entities) == [
        "AR_INVOICES", "MARINE_SALES_PLANNING_V", "W_MARINE_GL_SOURCE_AI"]
    assert sorted(ont.active()) == ["MARINE_SALES_PLANNING_V", "W_MARINE_GL_SOURCE_AI"]

    msp = ont.entity("MARINE_SALES_PLANNING_V")
    assert msp.fqn == "SANDBOX.MCA.MARINE_SALES_PLANNING_V"
    assert msp.date_column == "LIFT_ETA_DATE"
    assert len(msp.columns) == 22
    assert sorted(msp.metrics) == [
        "GP", "MARGIN", "NUM_INQUIRIES", "NUM_LOST", "NUM_WON", "VOLUME", "WIN_RATE"]
    assert msp.metrics["MARGIN"].sql == "SUM(GROSS_PROFIT) / NULLIF(SUM(FIXED_TONS), 0)"
    assert msp.columns["#_FIXTURES"].quoted is True
    assert msp.columns["#_FIXTURES"].role == "measure"
    assert msp.columns["LOC_NM"].friendly == "Port"
    assert len(msp.negative_constraints) == 21
    assert all(nc.observed for nc in msp.negative_constraints)
    assert msp.dual_purpose_pivot_column is None and msp.dual_purpose_pivot_value is None
    # Certified COALESCE placeholder, per entity. Sales keeps the default
    # ("COALESCE(col, 'Unknown') on dimension columns before grouping.");
    # GL overrides it ("COALESCE(<col>,'Unassigned') on every GROUP BY").
    # These two literals are what query_builder renders — see the GL
    # snapshots in test_query_builder_snapshots.py.
    assert msp.null_placeholder == "Unknown"

    gl = ont.entity("W_MARINE_GL_SOURCE_AI")
    assert gl.date_column == "PERIOD_DATE"
    assert len(gl.columns) == 15
    assert sorted(gl.metrics) == ["MONETARY_TOTAL"]
    assert gl.hierarchy_levels == [
        "CLASS6_Calc", "CLASS6", "CLASS5", "CLASS4", "CLASS3", "CLASS2", "CLASS1"]
    assert gl.dual_purpose_exclusion == "COALESCE(CLASS4,'') <> 'Volume'"
    assert gl.dual_purpose_pivot_column == "CLASS4"
    assert gl.dual_purpose_pivot_value == "Volume"
    assert gl.null_placeholder == "Unassigned"
    assert len(gl.negative_constraints) == 17


def test_planned_entity_is_not_queryable():
    ont = load()
    import pytest
    with pytest.raises(KeyError, match="planned"):
        ont.entity("AR_INVOICES")


def test_dimension_order_preserved():
    ont = load()
    dims = ont.entity("MARINE_SALES_PLANNING_V").dimensions()
    assert dims[0] == "CUST_NM" and "LOC_NM" in dims and len(dims) == 16


def test_get_ontology_is_cached_and_frozen():
    import pydantic
    import pytest

    from poseidon.core.ontology.loader import get_ontology
    a, b = get_ontology(), get_ontology()
    assert a is b
    msp = a.entity("MARINE_SALES_PLANNING_V")
    with pytest.raises(pydantic.ValidationError):
        msp.grain = "mutated"


# ---------------------------------------------------------------------------
# D16 row_scope (Phase 14 Task 6b): the optional per-entity scope declaration.
# Mechanism only -- the certified ontology declares it nowhere (see the pin at
# the bottom of this section), so every case below loads a FIXTURE yaml
# written to tmp_path rather than touching ontology/ontology.yml.
# ---------------------------------------------------------------------------

# One dimension column (the plausible scope key), one measure column (the
# certification mistake the Entity validator must catch), one date column and
# one metric -- the smallest entity the loader will parse at all.
_FIXTURE_TEMPLATE = """\
version: 2
entities:
  SCOPED_FIXTURE_V:
    fqn: SANDBOX.MCA.SCOPED_FIXTURE_V
    date_column: LIFT_ETA_DATE
{row_scope}
    columns:
      LIFT_ETA_DATE:
        type: DATE
        role: date
        friendly: Lift ETA
      PRIMARY_BRKR:
        type: VARCHAR
        role: dimension
        friendly: Broker
      GROSS_PROFIT:
        type: NUMBER
        role: measure
        friendly: Gross Profit
    metrics:
      GP:
        sql: SUM(GROSS_PROFIT)
        kind: sum
"""


def _fixture_path(tmp_path: Path, row_scope: str) -> Path:
    """Write a one-entity fixture ontology whose ``row_scope`` block is
    ``row_scope`` (already indented, or empty for "declares none")."""
    target = tmp_path / "fixture_ontology.yml"
    target.write_text(_FIXTURE_TEMPLATE.format(row_scope=row_scope), encoding="utf-8")
    return target


def test_row_scope_round_trips_column_and_claim(tmp_path: Path):
    """A valid declaration parses into the typed `RowScope`: the loader reads
    the optional block, not just the model (the Entity is built field by
    field, so an unparsed key would silently stay None)."""
    path = _fixture_path(tmp_path, "    row_scope:\n      column: PRIMARY_BRKR\n      claim: sub")

    entity = load(path).entity("SCOPED_FIXTURE_V")

    assert entity.row_scope is not None
    assert entity.row_scope.column == "PRIMARY_BRKR"
    assert entity.row_scope.claim == "sub"


def test_entity_without_a_row_scope_block_parses_to_none(tmp_path: Path):
    entity = load(_fixture_path(tmp_path, "")).entity("SCOPED_FIXTURE_V")

    assert entity.row_scope is None


def test_row_scope_on_an_unknown_column_is_rejected_at_load(tmp_path: Path):
    """A column that isn't on the entity at all is a certification mistake,
    caught at load -- the loader's own fail-fast posture, not a surprise at
    the first query."""
    path = _fixture_path(tmp_path, "    row_scope:\n      column: NOT_A_COLUMN\n      claim: sub")

    with pytest.raises(pydantic.ValidationError) as exc_info:
        load(path)

    assert "row_scope column 'NOT_A_COLUMN' is not a dimension of SCOPED_FIXTURE_V" in str(
        exc_info.value
    )


def test_row_scope_on_a_measure_column_is_rejected_at_load(tmp_path: Path):
    """GROSS_PROFIT exists, but a measure can never key a per-person scope --
    same rejection as an unknown column, since "not a dimension" is the
    property that matters."""
    path = _fixture_path(tmp_path, "    row_scope:\n      column: GROSS_PROFIT\n      claim: sub")

    with pytest.raises(pydantic.ValidationError) as exc_info:
        load(path)

    assert "row_scope column 'GROSS_PROFIT' is not a dimension of SCOPED_FIXTURE_V" in str(
        exc_info.value
    )


def test_an_empty_row_scope_mapping_is_rejected_at_load(tmp_path: Path):
    """`row_scope: {}` must NEVER parse as "declares no scope" -- that is the
    one shape in this mechanism that would fail OPEN. A half-written block
    would render every row for every caller with no signal at all: the
    vendored all-None pin below would stay green, because the entity really
    would have parsed to None."""
    path = _fixture_path(tmp_path, "    row_scope: {}")

    with pytest.raises(pydantic.ValidationError):
        load(path)


def test_a_present_but_empty_row_scope_block_is_rejected_at_load(tmp_path: Path):
    """The same fail-open shape in its other spelling: `row_scope:` with
    nothing under it, which YAML parses to None -- byte-identical, through
    ``dict.get``, to the key being absent. Only genuine ABSENCE means "this
    entity is unscoped" (see the loader's `_ROW_SCOPE_ABSENT` sentinel)."""
    path = _fixture_path(tmp_path, "    row_scope:")

    with pytest.raises(pydantic.ValidationError):
        load(path)


def test_a_non_mapping_row_scope_is_rejected_at_load(tmp_path: Path):
    """`row_scope: PRIMARY_BRKR` (the column name alone, a plausible
    half-remembered spelling of the block) is a ValidationError like every
    other malformed-ontology shape -- not the bare TypeError a ``RowScope(
    **raw)`` splat would raise for a non-mapping."""
    path = _fixture_path(tmp_path, "    row_scope: PRIMARY_BRKR")

    with pytest.raises(pydantic.ValidationError):
        load(path)


def test_row_scope_claim_is_restricted_to_the_two_identity_fields(tmp_path: Path):
    """`claim` is a Literal["sub", "email"] -- the two UserContext fields that
    can plausibly key a per-person scope today. Widening it later is a
    one-line model change; until then an unknown claim fails at load."""
    path = _fixture_path(tmp_path, "    row_scope:\n      column: PRIMARY_BRKR\n      claim: roles")

    with pytest.raises(pydantic.ValidationError):
        load(path)


def test_no_certified_entity_declares_row_scope():
    """THE FLIP PIN. D16 ships the row_scope MECHANISM with no policy: the
    vendored ``ontology/ontology.yml`` declares it on no entity, so every
    query this codebase renders today is byte-identical to one built before
    the mechanism existed (see test_query_builder_snapshots.py, whose
    existing snapshots are unchanged for exactly that reason).

    Removing this pin IS the deliberate act of onlining row scoping. It is
    not a stale test to delete when it starts failing: a failure here means
    an entity now declares a scope column, every query over it starts
    carrying a scope predicate, and every call path that reaches it must
    already thread a real caller identity through.
    """
    ont = load()

    declaring = {name: e.row_scope for name, e in ont.entities.items() if e.row_scope is not None}
    assert declaring == {}
