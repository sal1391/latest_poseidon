from poseidon.tasks.reporting.skills.monthly_performance.tests.payload_fixture import (
    minimal_payload,
    mockup_payload,
)
from poseidon.tasks.reporting.skills.monthly_performance.tools import grounding, questions

MODEL = [
    "Why did GP drop for Meridian Bunkering?",
    "Where else does Meridian lift fuel?",
    "Which deal class drove the margin compression?",
]


def _select(candidates, payload=None):
    payload = payload or mockup_payload()
    return questions.select_questions(candidates, payload, grounding.build_index(payload))


def test_model_questions_naming_payload_entities_are_kept_in_order():
    assert _select(MODEL) == MODEL


def test_a_question_naming_an_unknown_entity_is_dropped_not_rewritten_and_backfilled():
    picked = _select(["Why did Poseidon Shipping leave?", MODEL[0]])
    assert picked[0] == MODEL[0]
    assert len(picked) == 3
    assert not any("Poseidon" in q for q in picked)


def test_backfill_is_deterministic_and_names_the_largest_mover():
    assert _select([]) == [
        "Why did GP change for Meridian Bunkering?",
        "Where else does Meridian Bunkering lift fuel?",
        "What were the top ports in the report?",
    ]


def test_supplier_backfill_asks_which_customers_the_supplier_supplied():
    payload = mockup_payload()
    payload["perspective"] = "supplier"
    payload["entity"] = {"column": "SUPPLIER_NM", "label": "Supplier"}
    payload["scope"] = {
        "office_column": "PRIMARY_SUPPLY_TEAM_OFFICE",
        "office_value": "Med Supply Team",
        "label": "Med Supply Team (Supply Team Office)",
    }
    assert (
        _select([], payload)[1]
        == "Which customers did Meridian Bunkering supply in Med Supply Team?"
    )


def test_no_movers_skips_the_mover_questions():
    payload = mockup_payload()
    payload["movers"] = {"increases": [], "decreases": []}
    assert _select([], payload) == [
        "What were the top ports in the report?",
        "Which deal class drove the margin change?",
        "Which customers drove GP in August 2026?",
    ]


def test_duplicates_of_a_kept_question_are_not_backfilled_twice():
    picked = _select(
        ["What were the top ports in the report?", "what were the top ports in the report?"]
    )
    assert picked.count("What were the top ports in the report?") == 1
    assert len(picked) == 3


def test_metric_words_offices_and_the_first_word_are_not_entity_mentions():
    index = grounding.build_index(mockup_payload())
    payload = mockup_payload()
    assert questions.mention_resolves(
        "Did GP or Volume move more at Gibraltar Office?", index, payload
    )
    assert questions.mention_resolves(
        "Which deal class drove the margin change in August 2026?", index, payload
    )
    assert questions.mention_resolves("How did Harbor & Sons do?", index, payload)
    assert not questions.mention_resolves("How did Harbor & Daughters do?", index, payload)


def test_minimal_payload_always_yields_three():
    assert len(_select([], minimal_payload())) == 3


def test_a_model_question_with_an_unverified_figure_is_dropped():
    picked = _select(
        [
            "Why did GP fall 40% for Meridian Bunkering?",
            "Why did GP drop for Meridian Bunkering?",
        ]
    )
    assert "Why did GP fall 40% for Meridian Bunkering?" not in picked
    assert picked[0] == "Why did GP drop for Meridian Bunkering?"
    assert len(picked) == 3
