"""Offline: the snapshot client binds every query to one REPEATABLE READ,
read-only connection (D54) and the plain client still opens one per call.
A fake ``psycopg.connect`` records what the client asked for."""

import datetime as dt

import psycopg

from poseidon.core.data.specs import FrameQuerySpec, MetricQuerySpec, PeriodWindow
from poseidon.core.data.synthetic_client import SyntheticDataClient

APRIL = PeriodWindow(dt.date(2026, 4, 1), dt.date(2026, 5, 1))
SALES = "MARINE_SALES_PLANNING_V"


class _Cursor:
    def __init__(self):
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql = sql
        self._rows = [("ACME", 10.0, 2.0)] if "GROUP BY" in sql else [(10.0,)]

    def fetchall(self):
        return self._rows

    def fetchmany(self, n):
        return self._rows[:n]


class _Conn:
    opened = []

    def __init__(self):
        self.isolation_level = None
        self.read_only = None
        self.closed = False
        self.rolled_back = False
        _Conn.opened.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def cursor(self):
        return _Cursor()

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _fake_connect(monkeypatch):
    _Conn.opened.clear()
    monkeypatch.setattr(psycopg, "connect", lambda dsn, connect_timeout: _Conn())


def test_snapshot_runs_every_query_on_one_repeatable_read_connection(monkeypatch):
    _fake_connect(monkeypatch)
    client = SyntheticDataClient("postgresql://u:p@h/db")
    frame = FrameQuerySpec(SALES, ("GP", "VOLUME"), APRIL, ("CUST_NM",))
    total = MetricQuerySpec(SALES, ("GP",), APRIL)

    with client.snapshot() as snap:
        rows = snap.run_frame_query(frame).rows
        snap.run_metric_query(total)
        snap.run_metric_query(total)

    assert len(_Conn.opened) == 1
    conn = _Conn.opened[0]
    assert conn.isolation_level == psycopg.IsolationLevel.REPEATABLE_READ
    assert conn.read_only is True
    assert conn.closed is True
    assert conn.rolled_back is True
    assert rows[0].keys == ("ACME",) and rows[0].values == {"GP": 10.0, "VOLUME": 2.0}


def test_plain_client_still_opens_one_connection_per_call(monkeypatch):
    _fake_connect(monkeypatch)
    client = SyntheticDataClient("postgresql://u:p@h/db")
    client.run_metric_query(MetricQuerySpec(SALES, ("GP",), APRIL))
    client.run_metric_query(MetricQuerySpec(SALES, ("GP",), APRIL))
    assert len(_Conn.opened) == 2
    assert all(c.isolation_level is None for c in _Conn.opened)
