import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_upgrade_head_on_sqlite(tmp_path, monkeypatch):
    db = tmp_path / "mig.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db.as_posix()}")
    monkeypatch.setenv("S3_BUCKET", "poseidon-artifacts")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert db.exists()

    # Phase 10 Task 1: the chain must actually reach head, not just exit 0 --
    # `alembic upgrade head` would also exit 0 (a silent no-op) if a revision
    # were never chained onto its predecessor's down_revision, so `alembic
    # current` is checked explicitly rather than trusting the upgrade
    # command's own return code alone. Phase 11 Task 1 extended this same
    # check from 0004 to 0005 (run-log RLS, admin role, redaction support);
    # Phase 12 Task 1 extended it again, 0005 to 0006 (message_feedback); the
    # un-vote follow-up extended it once more, 0006 to 0007 (verdict becomes
    # nullable); Phase 13 Task 1 extends it again, 0007 to 0008
    # (personalization: user_profile/user_memory/memory_outbox) --
    # `alembic current` reports only the single revision at the tip, so this
    # assertion always names the CURRENT head, not every revision the chain
    # passed through on the way there.
    current = subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        cwd=BACKEND, capture_output=True, text=True,
    )
    assert current.returncode == 0, current.stderr
    assert "0008" in current.stdout, current.stdout
