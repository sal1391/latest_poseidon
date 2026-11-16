from poseidon.core.documents import ANONYMOUS_SUB, safe_filename

PARITY = [  # the same fixtures as web/src/lib/report-files.test.ts (Task 4)
    ("Acme  Marine Brief", "Acme Marine Brief"),
    ("A/S Rederi: Q3 Brief!", "A_S Rederi_ Q3 Brief_"),
    ('Rotterdam/"x" Office', "Rotterdam__x_ Office"),
    ("   ", "report"),
]


def test_safe_filename_parity_fixtures():
    for raw, expected in PARITY:
        assert safe_filename(raw) == expected


def test_anonymous_sub():
    assert ANONYMOUS_SUB == "dev|anonymous"


def test_reporting_skills_use_the_shared_helpers():
    from poseidon.tasks.reporting.skills.monthly_performance import skill as monthly
    from poseidon.tasks.reporting.skills.report_lookup import skill as lookup

    assert monthly.safe_filename is safe_filename
    assert not hasattr(lookup, "ANONYMOUS") and not hasattr(monthly, "ANONYMOUS")
