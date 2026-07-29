"""Chat titles via the ``utility`` role (doc 03 section 2).

Naming a conversation is the archetypal small job the tier map pushes down:
"anything a smaller model can do deterministically enough is pushed down".
It runs on ``utility`` (Nova Lite on Bedrock, a small Cortex model on
Cortex) with ``temperature: 0.0`` and a 512-token ceiling straight from
``models.yml`` -- this module names no model and no provider, only a role,
like every other caller above the seam.

The model's answer is never trusted verbatim. A title has to be one short
line that fits a sidebar, and models reliably wrap titles in quotes, pad
them, or run long no matter how the prompt is phrased. So the prompt ASKS
for the shape and :func:`_clean` ENFORCES it -- and the length the prompt
asks for is interpolated from :data:`TITLE_MAX_CHARS`, so the instruction
and the truncation can never disagree.
"""

from poseidon.core.llm.prompts import DEFAULT_PROMPTS_DIR, PromptRegistry
from poseidon.core.llm.roles import RoleClient

# The role and prompt this module is; named constants because tests assert
# on them and Phase 6 will want the same names when it wires titling in.
UTILITY_ROLE = "utility"
TITLE_PROMPT = "utility/title"

# Hard cap, in characters. Not a soft preference: the sidebar has a width,
# and a model that ignores the instruction still gets truncated here.
TITLE_MAX_CHARS = 60


def title_for(
    text: str, role_client: RoleClient, prompt_registry: PromptRegistry | None = None
) -> str:
    """A short chat title for ``text``, or ``""`` when the provider failed.

    ``""`` rather than a raised exception or a salvaged string: a transport
    failure's text ("bedrock error: ThrottlingException") is exactly the
    kind of thing that must never end up labelling someone's conversation,
    and a title is not worth failing a request over. The caller chooses the
    fallback (a timestamp, the first words of the question, nothing at all).

    ``prompt_registry`` defaults to the packaged prompts directory; Phase 6
    passes the one built from ``Settings.prompts_dir`` so a deploy that
    overrides prompts overrides this one too.
    """
    registry = PromptRegistry(DEFAULT_PROMPTS_DIR) if prompt_registry is None else prompt_registry
    system = registry.render(TITLE_PROMPT, max_chars=TITLE_MAX_CHARS)
    response = role_client.invoke(
        UTILITY_ROLE, system=system, messages=[{"role": "user", "content": text}]
    )
    if response.stop_reason == "error":
        return ""
    return _clean(response.text)


def _clean(raw: str) -> str:
    """Model output -> a title.

    ``" ".join(raw.split())`` collapses every whitespace run -- including
    the newlines a model adds when it decides to explain itself on a second
    line -- and strips the ends in one pass; a title with a line break in
    it would break the sidebar row it is rendered into. Then the surrounding
    quotes models add regardless of instruction, then the hard cap.

    Truncation is a plain slice with no ellipsis: an ellipsis would spend
    three of the sixty characters saying something the truncation already
    shows.
    """
    collapsed = " ".join(raw.split())
    unquoted = collapsed.strip("\"'").strip()
    return unquoted[:TITLE_MAX_CHARS]
