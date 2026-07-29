"""The stub provider (doc 03 section 1; doc 08's validation criterion): a
scripted :class:`~poseidon.core.llm.roles.LLMProvider` that replays
pre-built :class:`~poseidon.core.llm.types.LLMResponse` values instead of
calling a real model.

This is the other half of the stub/live seam :class:`~poseidon.core.llm.
roles.RoleClient` implements (see ``roles.py``): every offline test in this
codebase can run the SAME calling code a live call would use, with a
``StubProvider`` standing in for whichever provider a role is configured
for.
"""

from collections.abc import Sequence

from poseidon.core.llm.types import LLMResponse


class StubProvider:
    """Replays a scripted sequence of responses in order, recording every
    call made to it.

    ``script`` is copied into an internal queue at construction, so the
    caller's own sequence is never mutated and can be reused to build a
    second ``StubProvider`` for a second run of the same scenario.
    """

    def __init__(self, script: Sequence[LLMResponse]) -> None:
        self._script = list(script)
        self.calls: list[dict] = []

    def invoke(
        self, *, system: str, messages: list[dict], tools: list[dict], model: str, params: dict
    ) -> LLMResponse:
        """Record the request, then pop the next scripted response.

        Recording happens before the exhaustion check, not after, so
        ``calls`` holds one entry per call actually made -- including the
        call that exhausts the script -- which is what lets a caller tell
        "ran out of script" apart from "never called".
        """
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "tools": tools,
                "model": model,
                "params": params,
            }
        )
        if not self._script:
            raise AssertionError("stub script exhausted")
        return self._script.pop(0)
