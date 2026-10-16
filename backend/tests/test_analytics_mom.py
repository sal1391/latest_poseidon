"""Hand-computed expectations for the month-over-month math (reports
design 4.3, Phase 17 spec 5.4). mom-comparison ships no tests, so every
number here was derived by hand from the fixture below; the derivations
are in the comments so a reviewer can check them without running code."""

from random import Random

import pytest

from poseidon.core.analytics import mom

# Prior month (A) and current month (B), rows already rolled up per customer.
PRIOR = {
    "Alpha": {
        "GP": 100_000.0,
        "VOLUME": 4_000.0,
        "NUM_WON": 10.0,
        "NUM_INQUIRIES": 20.0,
        "NUM_LOST": 10.0,
    },
    "Beta": {
        "GP": 50_000.0,
        "VOLUME": 2_500.0,
        "NUM_WON": 5.0,
        "NUM_INQUIRIES": 10.0,
        "NUM_LOST": 5.0,
    },
    "Gamma": {
        "GP": 20_000.0,
        "VOLUME": 1_000.0,
        "NUM_WON": 2.0,
        "NUM_INQUIRIES": 4.0,
        "NUM_LOST": 2.0,
    },
}
CURRENT = {
    "Alpha": {
        "GP": 90_000.0,
        "VOLUME": 4_500.0,
        "NUM_WON": 12.0,
        "NUM_INQUIRIES": 20.0,
        "NUM_LOST": 8.0,
    },
    "Beta": {
        "GP": 65_000.0,
        "VOLUME": 2_500.0,
        "NUM_WON": 5.0,
        "NUM_INQUIRIES": 12.0,
        "NUM_LOST": 7.0,
    },
    "Delta": {
        "GP": 10_000.0,
        "VOLUME": 500.0,
        "NUM_WON": 1.0,
        "NUM_INQUIRIES": 2.0,
        "NUM_LOST": 1.0,
    },
}


def test_totals_recompute_margin_and_win_rate_from_sums():
    t = mom.totals(PRIOR.values())
    assert t["GP"] == 170_000.0 and t["VOLUME"] == 7_500.0
    assert t["MARGIN"] == pytest.approx(170_000 / 7_500)  # 22.666..., never an average of margins
    assert t["NUM_WON"] == 17.0 and t["NUM_INQUIRIES"] == 34.0 and t["NUM_LOST"] == 17.0
    assert t["WIN_RATE"] == 0.5


def test_totals_treat_none_cells_as_zero_and_empty_volume_as_zero_margin():
    t = mom.totals([{"GP": None, "VOLUME": None}])
    assert t["GP"] == 0.0 and t["MARGIN"] == 0.0 and t["WIN_RATE"] == 0.0


def test_compute_kpis_matches_the_hand_derivation():
    k = mom.compute_kpis(PRIOR.values(), CURRENT.values())
    assert list(k) == list(mom.KPI_METRICS)
    # VOLUME 7,500 -> 7,500: flat.
    assert k["VOLUME"] == mom.Delta(7_500.0, 7_500.0, 0.0, 0.0)
    # GP 170,000 -> 165,000: -5,000, -5,000/170,000 = -2.94...%.
    assert k["GP"].change == -5_000.0
    assert k["GP"].pct_change == pytest.approx(-2.9411764705)
    # MARGIN 22.67 -> 22.00.
    assert k["MARGIN"].prior == pytest.approx(22.6666666667)
    assert k["MARGIN"].current == 22.0
    # NUM_WON 17 -> 18 (+1, +5.88%); NUM_INQUIRIES 34 -> 34 (flat); NUM_LOST 17 -> 16.
    assert k["NUM_WON"].change == 1.0 and k["NUM_WON"].pct_change == pytest.approx(5.8823529412)
    assert k["NUM_INQUIRIES"].change == 0.0
    assert k["NUM_LOST"].change == -1.0
    # WIN_RATE 17/34 = 0.5 -> 18/34 = 0.5294...
    assert k["WIN_RATE"].prior == 0.5
    assert k["WIN_RATE"].current == pytest.approx(0.5294117647)


def test_merge_breakdowns_outer_joins_zero_fills_and_sorts_by_key():
    merged = {row.key: row for row in mom.merge_breakdowns(PRIOR, CURRENT)}
    assert [r.key for r in mom.merge_breakdowns(PRIOR, CURRENT)] == [
        "Alpha",
        "Beta",
        "Delta",
        "Gamma",
    ]
    alpha = merged["Alpha"]
    assert alpha.status == "both"
    assert alpha.metrics["GP"] == mom.Delta(100_000.0, 90_000.0, -10_000.0, -10.0)
    assert alpha.metrics["VOLUME"] == mom.Delta(4_000.0, 4_500.0, 500.0, 12.5)
    assert alpha.metrics["MARGIN"] == mom.Delta(25.0, 20.0, -5.0, -20.0)
    assert alpha.metrics["NUM_WON"] == mom.Delta(10.0, 12.0, 2.0, 20.0)
    gamma = merged["Gamma"]
    assert gamma.status == "lost"
    assert gamma.metrics["GP"] == mom.Delta(20_000.0, 0.0, -20_000.0, -100.0)
    assert gamma.metrics["MARGIN"].current == 0.0
    delta = merged["Delta"]
    assert delta.status == "new"
    assert delta.metrics["GP"] == mom.Delta(0.0, 10_000.0, 10_000.0, None)  # zero-base rule
    assert delta.metrics["MARGIN"] == mom.Delta(0.0, 20.0, 20.0, None)


def test_driver_rule_verbatim_from_mom_comparison():
    merged = {row.key: row for row in mom.merge_breakdowns(PRIOR, CURRENT)}
    # Alpha: vol_effect = 500 * 25 = 12,500; margin_effect = -5 * 4,500 = -22,500;
    # ratio = 12,500 / 35,000 = 0.357 -> both.
    assert mom.driver(merged["Alpha"]) == "both"
    # Beta: volume unchanged -> vol_effect 0; margin_effect = 6 * 2,500 -> margin-driven.
    assert mom.driver(merged["Beta"]) == "margin-driven"
    assert mom.driver(merged["Gamma"]) == "lost"
    assert mom.driver(merged["Delta"]) == "new"


def _row(gp_a, vol_a, gp_b, vol_b):
    return mom.merge_breakdowns(
        {"X": {"GP": gp_a, "VOLUME": vol_a}}, {"X": {"GP": gp_b, "VOLUME": vol_b}}
    )[0]


def test_driver_boundaries_at_0_65_and_0_35_are_both():
    # m_a = 1 in every case so vol_effect = dvol; margin_effect = dmargin * vol_b.
    assert mom.driver(_row(35, 35, 135, 100)) == "both"  # 65 vs 35 -> ratio 0.65
    assert mom.driver(_row(34, 34, 134, 100)) == "volume-driven"  # 66 vs 34 -> 0.66
    assert mom.driver(_row(65, 65, 165, 100)) == "both"  # 35 vs 65 -> 0.35
    assert mom.driver(_row(66, 66, 166, 100)) == "margin-driven"  # 34 vs 66 -> 0.34
    assert mom.driver(_row(50, 10, 50, 10)) == "unchanged"


def test_zero_prior_margin_yields_a_null_pct_and_a_margin_driver():
    """Review Focus 3: prior volume present, prior GP zero."""
    row = _row(0.0, 100.0, 50.0, 100.0)
    assert row.status == "both"
    assert row.metrics["GP"].pct_change is None
    assert row.metrics["MARGIN"] == mom.Delta(0.0, 0.5, 0.5, None)
    assert mom.driver(row) == "margin-driven"
    assert mom.display(row.metrics["GP"], "money")["pct"] == "new"


def test_top_movers_by_gp_change_with_new_and_lost_taking_part():
    movers = mom.top_movers(mom.merge_breakdowns(PRIOR, CURRENT), n=5)
    assert [(m.row.key, m.driver) for m in movers.increases] == [
        ("Beta", "margin-driven"),
        ("Delta", "new"),
    ]
    assert [(m.row.key, m.driver) for m in movers.decreases] == [
        ("Gamma", "lost"),
        ("Alpha", "both"),
    ]
    assert [
        m.row.key for m in mom.top_movers(mom.merge_breakdowns(PRIOR, CURRENT), n=1).decreases
    ] == ["Gamma"]


def test_new_lost_sorted_by_absolute_gp():
    nl = mom.new_lost(mom.merge_breakdowns(PRIOR, CURRENT))
    assert [r.key for r in nl.new] == ["Delta"]
    assert [r.key for r in nl.lost] == ["Gamma"]


def test_ranked_carries_an_other_row_and_a_footer_equal_to_the_kpi():
    merged = mom.merge_breakdowns(PRIOR, CURRENT)
    r = mom.ranked(merged, by="GP", n=2, noun="customers")
    assert [row.key for row in r.rows] == ["Alpha", "Beta"]
    assert r.other is not None and r.other.key == "Other (2 customers)"
    assert r.other.metrics["GP"] == mom.Delta(20_000.0, 10_000.0, -10_000.0, -50.0)
    assert r.other.metrics["VOLUME"] == mom.Delta(1_000.0, 500.0, -500.0, -50.0)
    assert r.other.metrics["MARGIN"] == mom.Delta(20.0, 20.0, 0.0, 0.0)
    assert r.footer.key == "Total"
    kpis = mom.compute_kpis(PRIOR.values(), CURRENT.values())
    assert r.footer.metrics["GP"] == kpis["GP"]
    assert r.footer.metrics["VOLUME"] == kpis["VOLUME"]
    assert r.footer.metrics["MARGIN"] == kpis["MARGIN"]


def test_ranked_with_everything_shown_has_no_other_row():
    r = mom.ranked(mom.merge_breakdowns(PRIOR, CURRENT), by="VOLUME", n=10)
    assert r.other is None
    assert [row.key for row in r.rows] == ["Alpha", "Beta", "Delta", "Gamma"]


def test_ranked_rejects_an_unknown_metric():
    with pytest.raises(ValueError, match="by must be one of"):
        mom.ranked(mom.merge_breakdowns(PRIOR, CURRENT), by="NUM_WON")


def test_footer_equals_kpi_exactly_for_random_books():
    """Property: for any book, every ranked table's footer equals the KPI
    built from the same rows, by exact equality (same totals() call)."""
    rng = Random(1391)
    for _ in range(50):
        keys = [f"E{i}" for i in range(rng.randint(1, 12))]
        prior = {
            k: {
                "GP": rng.choice([0.0, rng.uniform(-5e4, 2e5)]),
                "VOLUME": rng.choice([0.0, rng.uniform(0, 9e3)]),
            }
            for k in keys
            if rng.random() > 0.2
        }
        current = {
            k: {
                "GP": rng.choice([0.0, rng.uniform(-5e4, 2e5)]),
                "VOLUME": rng.choice([0.0, rng.uniform(0, 9e3)]),
            }
            for k in keys
            if rng.random() > 0.2
        }
        merged = mom.merge_breakdowns(prior, current)
        kpis = mom.compute_kpis(prior.values(), current.values())
        for by in ("GP", "VOLUME"):
            r = mom.ranked(merged, by=by, n=3)
            assert r.footer.metrics["GP"] == kpis["GP"]
            assert r.footer.metrics["VOLUME"] == kpis["VOLUME"]
            shown = sum(row.metrics["GP"].current for row in r.rows)
            other = r.other.metrics["GP"].current if r.other else 0.0
            assert mom.reconciles(shown + other, r.footer.metrics["GP"].current)


def test_share_and_reconciles():
    assert mom.share(20.0, 100.0) == 20.0
    assert mom.share(5.0, 0.0) is None
    # 100.005 is not representable; 100.01 sits clearly past the half-cent tolerance.
    assert mom.reconciles(100.004, 100.0) and not mom.reconciles(100.01, 100.0)


def test_display_strings_follow_the_mom_comparison_rounding_rules():
    assert mom.fmt_value(1_182_900.4, "money") == "1,182,900"
    assert mom.fmt_value(44_210.0, "tons") == "44,210"
    assert mom.fmt_value(26.756, "margin") == "26.76"
    assert mom.fmt_value(298.0, "count") == "298"
    assert mom.fmt_value(0.5612, "rate") == "56.1%"
    assert mom.fmt_change(2_350.0, "tons") == "+2,350"
    assert mom.fmt_change(-53_500.0, "money") == "-53,500"
    assert mom.fmt_change(0.0, "money") == "flat"
    assert mom.fmt_change(-2.78, "margin") == "-2.78"
    assert mom.fmt_change(-0.053, "rate") == "-5.3 pts"
    assert mom.fmt_pct(5.6, 2_350.0) == "+5.6%"
    assert mom.fmt_pct(-4.3, -53_500.0) == "-4.3%"
    assert mom.fmt_pct(0.0, 0.0) == "flat"
    assert mom.fmt_pct(None, 10_000.0) == "new"
    assert mom.fmt_pct(None, 0.0) == "flat"  # zero to zero is flat, never "new"
    assert mom.display(mom.Delta(1_236_400.0, 1_182_900.0, -53_500.0, -4.3273), "money") == {
        "prior": "1,236,400",
        "current": "1,182,900",
        "change": "-53,500",
        "pct": "-4.3%",
    }


def test_module_is_ascii_on_disk():
    from pathlib import Path

    import poseidon.core.analytics.mom as module

    Path(module.__file__).read_bytes().decode("ascii")


def test_singular_noun():
    assert mom.singular("customers") == "customer"
    assert mom.singular("suppliers") == "supplier"
    assert mom.singular("ports") == "port"
    assert mom.singular("deal classes") == "deal class"


def test_other_row_with_one_entity_reads_singular():
    # three merged rows ranked with n=2 leave exactly one in the Other row
    merged = mom.merge_breakdowns(PRIOR, CURRENT)
    assert len(merged) == 4
    assert mom.ranked(merged, "GP", 3, "customers").other.key == "Other (1 customer)"
    assert mom.ranked(merged, "GP", 2, "customers").other.key == "Other (2 customers)"


def test_zero_displays_never_show_a_signed_zero():
    assert mom.fmt_value(-0.3, "money") == "0"
    assert mom.fmt_value(-0.001, "margin") == "0.00"
    assert mom.fmt_change(0.3, "money") == "flat"
    assert mom.fmt_change(-0.3, "money") == "flat"
    assert mom.fmt_change(-0.004, "margin") == "flat"
    assert mom.fmt_change(0.0004, "rate") == "flat"
    assert mom.fmt_pct(0.04, 0.3) == "flat"
    assert mom.fmt_pct(-0.04, -0.3) == "flat"
    # rounding still rounds; only a zero display is polished
    assert mom.fmt_change(0.6, "money") == "+1"
