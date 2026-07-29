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

    gl = ont.entity("W_MARINE_GL_SOURCE_AI")
    assert gl.date_column == "PERIOD_DATE"
    assert len(gl.columns) == 15
    assert sorted(gl.metrics) == ["MONETARY_TOTAL"]
    assert gl.hierarchy_levels == [
        "CLASS6_Calc", "CLASS6", "CLASS5", "CLASS4", "CLASS3", "CLASS2", "CLASS1"]
    assert gl.dual_purpose_exclusion == "COALESCE(CLASS4,'') <> 'Volume'"
    assert gl.dual_purpose_pivot_column == "CLASS4"
    assert gl.dual_purpose_pivot_value == "Volume"
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
