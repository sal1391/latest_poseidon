"""Phase-1 mock chat API. The routes and SSE protocol are the real contract
(docs/architecture/01-frontend.md §5); Phase 6 replaces the internals."""

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["chat-mock"])

_conversations: dict[str, dict[str, Any]] = {}
_messages: dict[str, list[dict[str, Any]]] = {}
_feedback: dict[str, dict[str, Any]] = {}

_ANSWER_CHUNKS = [
    "Three customers drove most of April's gross profit in Singapore:\n\n",
    "1. **Northstar Lines** — $412.4K\n",
    "2. **Blue Anchor Marine** — $268.0K\n",
    "3. **Crestline Freight** — $203.7K\n\n",
    "Northstar Lines also expanded its Singapore–Jakarta rotation recently — ",
    "ask me for the news summary if useful.",
]


class SendBody(BaseModel):
    text: str
    client_turn_key: str | None = None


class FeedbackBody(BaseModel):
    # verdict: str | None -- un-vote follow-up to Phase 12: None clears a
    # previously recorded verdict, kept in sync with live_chat.py's own
    # FeedbackBody (that module's own docstring: "Same shape mock_chat.py's
    # own FeedbackBody accepts").
    verdict: str | None
    comment: str | None = None


def _sse(name: str, payload: dict) -> str:
    return f"id: {payload['event_seq']}\nevent: {name}\ndata: {json.dumps(payload)}\n\n"


def _message(role: str, parts: list[dict]) -> dict:
    return {"id": str(uuid.uuid4()), "role": role, "parts": parts}


@router.post("/conversations", status_code=201)
def create_conversation() -> dict:
    cid = str(uuid.uuid4())
    conversation = {"id": cid, "title": "New chat"}
    opener = _message("assistant", [
        {"kind": "text", "payload": {"markdown": "Ask about your data, or pick a flow:"}},
        {"kind": "chips", "payload": {"options": [
            {"id": "existing_customer", "label": "Existing customer"},
            {"id": "new_prospect", "label": "New customer prospect"},
        ]}},
    ])
    _conversations[cid] = conversation
    _messages[cid] = [opener]
    return {"conversation": conversation, "opener": opener}


@router.get("/conversations")
def list_conversations() -> dict:
    """``{"items": [...], "next_cursor": null}`` -- final-review wave, I-2:
    matches live_chat.py's real envelope (Phase 10 Task 3) so the frontend
    works against EITHER backend. This mock has exactly one page of
    conversations (everything ever created this process, held in memory);
    ``next_cursor`` is therefore always null -- no real pagination is
    built here, unlike live_chat.py's cursor-encoded next page."""
    return {"items": list(reversed(list(_conversations.values()))), "next_cursor": None}


@router.get("/conversations/{cid}/messages")
def get_messages(cid: str) -> dict:
    """Same envelope change as :func:`list_conversations` above, same
    reason: exactly one page, ``next_cursor`` always null."""
    if cid not in _messages:
        raise HTTPException(404, detail="unknown conversation")
    return {"items": _messages[cid], "next_cursor": None}


@router.post("/conversations/{cid}/messages")
def send_message(cid: str, body: SendBody) -> StreamingResponse:
    if cid not in _messages:
        raise HTTPException(404, detail="unknown conversation")
    _messages[cid].append(_message("user", [
        {"kind": "text", "payload": {"markdown": body.text}}]))
    message_id = str(uuid.uuid4())
    turn_id = str(uuid.uuid4())
    # Register the assistant message before the first frame goes out, then fill
    # it in place as the turn runs. The UI renders its feedback row as soon as
    # the message appears, so feedback can arrive mid-stream: publishing the id
    # up front is what makes `_known_message` true for that whole window
    # instead of only after `done`.
    assistant: dict[str, Any] = {"id": message_id, "role": "assistant", "parts": []}
    _messages[cid].append(assistant)

    async def stream():
        event_seq = 0

        def ev(name: str, **fields) -> str:
            nonlocal event_seq
            event_seq += 1
            return _sse(name, {"turn_id": turn_id, "message_id": message_id,
                               "event_seq": event_seq, **fields})

        turn_index = sum(1 for m in _messages[cid] if m["role"] == "user")
        yield ev("accepted", turn_index=turn_index)
        if "!error" in body.text:
            # The assistant message stays in the transcript with empty parts —
            # the error is a stream event, not a persisted part, in this mock.
            yield ev("error", code="mock_failure",
                     message="Mock failure requested",
                     hint="Remove !error from your message")
            return
        steps = [
            (1, "top_customers", "internal",
             "Running skill · top_customers (GP · Singapore · Apr 2026)",
             "top_customers · done · 0.3s"),
            (2, "web_research", "perplexity",
             "Calling Perplexity — marine news search…",
             "Perplexity — 3 sources"),
        ]
        for tool_seq, tool, server, start_label, done_label in steps:
            yield ev("tool", tool_seq=tool_seq, tool=tool, server=server,
                     status="start", label=start_label)
            await asyncio.sleep(0.35)
            done = {"tool_seq": tool_seq, "tool": tool, "server": server,
                    "status": "done", "label": done_label}
            assistant["parts"].append({"kind": "tool_event", "payload": done})
            yield ev("tool", **done)
        text = ""
        for chunk in _ANSWER_CHUNKS:
            text += chunk
            yield ev("token", text=chunk)
            await asyncio.sleep(0.12)
        assistant["parts"].append({"kind": "text", "payload": {"markdown": text}})
        yield ev("done", usage={"input_tokens": 0, "output_tokens": 0})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@router.post("/messages/{mid}/feedback", status_code=204)
def upsert_feedback(mid: str, body: FeedbackBody) -> None:
    if body.verdict not in ("up", "down", None):
        raise HTTPException(422, detail="verdict must be up, down, or null")
    if not _known_message(mid):
        raise HTTPException(404, detail="unknown message")
    if body.verdict is None:
        # Un-vote: clear the entry rather than leaving a stale
        # {"verdict": None, ...} around -- get_feedback's existing
        # 404-if-absent behavior below stays consistent with the real
        # backend's new "cleared vote reads back like never-voted" contract
        # (poseidon.core.chat.feedback.UserFeedback.get's own docstring).
        # .pop(..., None): clearing a message with no prior entry is a
        # legitimate no-op, not an error.
        _feedback.pop(mid, None)
        return
    _feedback[mid] = {"verdict": body.verdict, "comment": body.comment}


@router.get("/messages/{mid}/feedback")
def get_feedback(mid: str) -> dict:
    if mid not in _feedback:
        raise HTTPException(404, detail="no feedback")
    return _feedback[mid]


def _known_message(mid: str) -> bool:
    return any(m["id"] == mid for msgs in _messages.values() for m in msgs)
