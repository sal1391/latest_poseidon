"""The model narrative: one forced tool call, the D67 grounding check, one retry.

The model sees the computed block only -- display strings, never a raw
float -- so every figure it can quote is one the grounding index holds. The
block is built from the payload after the template narrative is rendered,
so the model, the template and the index all read the same strings.

Outcomes (the caller always keeps the template narrative):

- grounded: the model markdown is used, its questions are candidates;
- malformed (provider error, no or wrong tool call, bad arguments): the
  template is used and the flag says why;
- ungrounded twice (or ungrounded, then malformed): the model prose is
  withheld, the flag carries the unverified-figure count, and the questions
  from the last well-formed reply are still offered to ``select_questions``,
  which filters them.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from poseidon.core.llm.prompt_tree import PromptSpec
from poseidon.core.llm.roles import RoleClient
from poseidon.core.llm.types import LLMResponse

from .grounding import GroundingIndex, GroundingReport, check

TOOL_NAME = "reporting.narrate"
ROLE = "synthesis"
_LIST_CAP = 10
_ROW_METRICS = ("GP", "VOLUME", "MARGIN")

NARRATE_TOOL = {
    "name": TOOL_NAME,
    "description": (
        "Return the report narrative, three to five insights, any context notes and "
        "exactly three suggested questions."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "narrative_md": {"type": "string"},
            "insights": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 3,
                "maxItems": 5,
            },
            "context_notes": {"type": "array", "items": {"type": "string"}},
            "suggested_questions": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 3,
                "maxItems": 3,
            },
        },
        "required": ["narrative_md", "insights", "context_notes", "suggested_questions"],
    },
}

_PROVIDER_ERROR = "provider error"
_MALFORMED = "malformed tool output"
_RETRY_HEADER = (
    "\n\nYour previous narrative failed the figure check. These sentences contain "
    "figures that are not in the data:\n"
)
_RETRY_FOOTER = "\nRewrite the narrative using only the display strings given."


@dataclass(frozen=True)
class Narrative:
    llm_md: str | None
    grounded: bool
    flags: list[str]
    model_questions: list[str]
    proof_line: str


@dataclass(frozen=True)
class _Reply:
    llm_md: str
    grounding_text: str
    questions: list[str]


def _row_display(row: Mapping[str, Any]) -> dict[str, Any]:
    return {m: dict(row["display"][m]) for m in _ROW_METRICS}


def build_computed_block(payload: Mapping[str, Any]) -> dict:
    """Display strings only -- never a raw float."""
    periods = payload["periods"]
    movers = payload["movers"]
    new_lost = payload["new_lost"]
    block: dict[str, Any] = {
        "scope": payload["scope"]["label"],
        "periods": {
            "prior": periods["prior"]["label"],
            "current": periods["current"]["label"],
            "window_kind": periods["window_kind"],
        },
        "entity_label": payload["entity"]["label"],
        "kpis": {name: dict(delta["display"]) for name, delta in payload["kpis"].items()},
        "movers": {
            side: [
                {"key": m["key"], "driver": m["driver"], **_row_display(m)} for m in movers[side]
            ]
            for side in ("increases", "decreases")
        },
        "new": [{"key": r["key"], **_row_display(r)} for r in new_lost["new"][:_LIST_CAP]],
        "lost": [{"key": r["key"], **_row_display(r)} for r in new_lost["lost"][:_LIST_CAP]],
    }
    context = payload["entity_context"]
    if context is not None:
        block["entity_context"] = {
            name: {
                "book": {m: dict(c["book"][m]["display"]) for m in _ROW_METRICS},
                "share_of_book": dict(c["share_of_book"]["display"]),
                "reconciliation": dict(c["reconciliation"]["display"]),
                "offsets": [{"key": o["key"], "change": o["display"]} for o in c["offsets"]],
                "ports_rest_of_book": [
                    {"key": p["key"], "change": p["display"]} for p in c["ports_rest_of_book"]
                ],
            }
            for name, c in context.items()
        }
    return block


def _str_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def _parse(response: LLMResponse) -> _Reply | str:
    """The well-formed reply, or the failure reason."""
    if response.stop_reason == "error":
        return _PROVIDER_ERROR
    if not response.tool_calls or response.tool_calls[0].name != TOOL_NAME:
        return _MALFORMED
    args = response.tool_calls[0].arguments
    if not isinstance(args, Mapping):
        return _MALFORMED
    narrative_md = args.get("narrative_md")
    insights = args.get("insights")
    notes = args.get("context_notes")
    questions = args.get("suggested_questions")
    if not (
        isinstance(narrative_md, str)
        and _str_list(insights)
        and _str_list(notes)
        and _str_list(questions)
    ):
        return _MALFORMED
    llm_md = narrative_md
    if insights:
        llm_md += "\n\n## Insights\n" + "\n".join(f"- {insight}" for insight in insights)
    if notes:
        llm_md += "\n\n## Notes\n" + "\n".join(f"- {note}" for note in notes)
    grounding_text = llm_md  # the insights are inside it now
    return _Reply(llm_md=llm_md, grounding_text=grounding_text, questions=list(questions))


def _failed(reason: str) -> Narrative:
    return Narrative(
        None,
        False,
        [f"model narrative failed: {reason}"],
        [],
        f"narrative: model narrative failed ({reason}); template used",
    )


def _withheld(unverified: int, questions: list[str]) -> Narrative:
    return Narrative(
        None,
        False,
        [f"AI narrative withheld: {unverified} unverified figures"],
        questions,
        f"narrative: AI narrative withheld ({unverified} unverified figures); template used",
    )


def _grounded(reply: _Reply, report: GroundingReport, prompt: PromptSpec) -> Narrative:
    return Narrative(
        reply.llm_md,
        True,
        [],
        reply.questions,
        f"narrative: synthesis role, prompt {prompt.id} v{prompt.version}, grounded "
        f"({report.checked} of {report.checked} figures)",
    )


def _call(llm: RoleClient, prompt: PromptSpec, content: str) -> LLMResponse:
    try:
        return llm.invoke(
            ROLE,
            system=prompt.system,
            messages=[{"role": "user", "content": content}],
            tools=[NARRATE_TOOL],
            tool_choice=TOOL_NAME,
        )
    except Exception:  # noqa: BLE001 - any provider failure falls back to the template
        return LLMResponse(
            text="", tool_calls=(), stop_reason="error", input_tokens=0, output_tokens=0
        )


def narrate(
    payload: Mapping[str, Any], llm: RoleClient, prompt: PromptSpec, index: GroundingIndex
) -> Narrative:
    block = build_computed_block(payload)
    content = (
        "Computed figures (JSON):\n" + json.dumps(block, indent=1) + "\n\nWrite the narrative now."
    )
    first = _parse(_call(llm, prompt, content))
    if isinstance(first, str):
        return _failed(first)
    report = check(first.grounding_text, index)
    if report.ok:
        return _grounded(first, report, prompt)

    retry_content = (
        content
        + _RETRY_HEADER
        + "\n".join(f"- {v.sentence}" for v in report.violations)
        + _RETRY_FOOTER
    )
    second = _parse(_call(llm, prompt, retry_content))
    if isinstance(second, str):
        return _withheld(report.unverified, first.questions)
    second_report = check(second.grounding_text, index)
    if second_report.ok:
        return _grounded(second, second_report, prompt)
    return _withheld(second_report.unverified, second.questions)
