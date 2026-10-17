"""Phase 17 PR A2 Task 11: the whole monthly-report flow over HTTP against the real database.

Marked ``pg``. It imports the module-level guards (skip when ``DATABASE_URL`` is unset or
unseeded) and the HTTP helpers from ``test_chat_e2e_scripted``. The report flow's three steps,
the report turn, the stored run row and a scoped follow-up are driven through a real
``create_app(chat_mode="live")`` ASGI app with the stub LLM. The latest complete month is
chosen by the real clock (June 2026 on the synthetic dataset), so the month is read off the
chips rather than typed.
"""

import re
import uuid

import httpx
import pytest

from poseidon.core.chat.report_flow import PERSPECTIVE_CHIPS
from poseidon.core.db import build_engine
from poseidon.reports.store import ReportStore
from tests.test_chat_e2e_scripted import (
    _DSN,
    _dev_user,
    _drop_artifact_store,
    _headers,
    _settings,
    anyio_backend,  # noqa: F401 - the anyio fixture the pg suite shares
    read_sse,
)

pytestmark = pytest.mark.pg


def _names(events):
    return [name for name, _data in events]


def _part(events, kind):
    return next(d for n, d in events if n == "part" and d["kind"] == kind)


@pytest.mark.anyio
async def test_monthly_report_flow_over_http_then_a_scoped_follow_up():
    from poseidon.api.app import create_app

    app = create_app(_settings())
    _drop_artifact_store(app)
    user = _dev_user("report-flow")
    sub = f"dev|{user}"
    headers = _headers(user)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=headers)).json()["conversation"][
            "id"
        ]

        async def say(text):
            return await read_sse(client, cid, text, str(uuid.uuid4()), headers)

        # 1. entry -> perspective chips
        ev1 = await say("start a monthly report")
        assert _names(ev1) == ["accepted", "part", "part", "done"]
        assert _part(ev1, "chips")["payload"]["options"] == PERSPECTIVE_CHIPS

        # 2. perspective -> office chips
        ev2 = await say("customer office report")
        office_chips = _part(ev2, "chips")["payload"]["options"]
        assert office_chips
        office = office_chips[0]["send_text"]

        # 3. office -> month chips
        ev3 = await say(office)
        month_chips = _part(ev3, "chips")["payload"]["options"]
        assert month_chips
        month_text = month_chips[0]["send_text"]

        # 4. month -> the report turn
        ev4 = await say(month_text)
        names4 = _names(ev4)
        report = _part(ev4, "report")
        proof = _part(ev4, "proof")
        flags = report["payload"]["narrative"]["flags"]
        pdf_rendered = not any(f.startswith("PDF not rendered:") for f in flags)
        expected = ["accepted", "tool", "tool", "part", "part"]
        if pdf_rendered:
            expected.append("part")
        expected += ["part", "done"]
        assert names4 == expected
        assert [d["kind"] for n, d in ev4 if n == "part"][-1] == "chips"
        assert report["payload"]["scope"]["office_value"] == office
        assert len(_part(ev4, "chips")["payload"]["options"]) == 3
        assert flags[0] == "model narrative skipped: stub mode"
        run_id = next(
            m.group(1)
            for line in proof["payload"]["lines"]
            if (m := re.fullmatch(r"Run: report_run (\S+)", line))
        )

        # 5. the stored row belongs to this conversation (the PDF route is Next.js, not Python)
        store = ReportStore(build_engine(_DSN), app_role=app.state.settings.database_app_role)
        row = store.get_run(sub, run_id)
        assert row is not None
        assert row.conversation_id == cid

        # 6. a scoped follow-up carries the office
        ev6 = await say("top GP customers for Port of Singapore in June 2026")
        tools6 = [d["tool"] for n, d in ev6 if n == "tool"]
        assert "data_qa.metric_query" in tools6
        lines6 = _part(ev6, "proof")["payload"]["lines"]
        assert lines6[-1] == (
            f"Office scope: CUSTOMER_TEAM_NAME = {office} (carried; scope=office)"
        )
        assert any(f"CUSTOMER_TEAM_NAME IN ({office})" in line for line in lines6)

    # 7. the conversation's slots carry the report and the mode is back to default
    slots = app.state.history_store.for_user(sub).read_state(cid)["slots"]
    assert slots["report_ref"] == run_id
    assert slots["mode"] == "default"
