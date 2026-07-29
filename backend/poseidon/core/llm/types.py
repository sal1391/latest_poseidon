"""Normalized provider response types (doc 03 section 1).

Every provider -- Bedrock, Cortex (Phase 14), and the stub -- speaks its own
wire format, so nothing above the provider seam should ever see one: a
Converse response dict, a Cortex completion, and a scripted stub value are
all normalized to :class:`LLMResponse` before ``RoleClient`` hands them
back. That normalization is what lets a caller be written once and run
unchanged against whichever provider ``LLM_MODE``/``LLM_PROFILE`` select
(decision D21).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolCall:
    """One tool the model asked to call, already split out of the response.

    ``arguments`` is parsed JSON, not yet validated -- dispatch does that,
    against the target skill's own ``Args`` model (doc 02).
    """

    id: str
    name: str  # skill id, e.g. "data_qa.metric_query"
    arguments: dict[str, object]  # parsed JSON args (validation happens at dispatch)


@dataclass(frozen=True)
class LLMResponse:
    """One provider call's complete, normalized result.

    ``tool_calls`` is a tuple, not a list, so a frozen instance can never
    have its call list mutated out from under a caller that already
    inspected it.
    """

    text: str  # "" when pure tool_use
    tool_calls: tuple[ToolCall, ...]
    stop_reason: str  # "tool_use" | "end_turn" | "error"
    input_tokens: int
    output_tokens: int
