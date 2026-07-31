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

    # Phase 10 Task 1: the chain must actually reach 0004 (chat history +
    # RLS), not just exit 0 -- `alembic upgrade head` would also exit 0 (a
    # silent no-op) if 0004 were never chained onto 0003's down_revision, so
    # `alembic current` is checked explicitly rather than trusting the
    # upgrade command's own return code alone.
    current = subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        cwd=BACKEND, capture_output=True, text=True,
    )
    assert current.returncode == 0, current.stderr
    assert "0004" in current.stdout, current.stdout
