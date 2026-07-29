"""The config-driven LLM provider layer (doc 03): normalized types, the
model-profile-to-role resolver, the stub/live provider seam, the prompt
registry/assembly layer, and the Bedrock provider.

Phase 5 builds this package task by task; this ``__init__`` re-exports
whatever each task adds so a caller never needs to know which submodule a
name lives in. As of Task 4 the package is complete: the normalized
response types, the role client, the stub provider, the prompt
registry/assembly functions, ``BedrockProvider``, the agent loop
(``run_turn`` and its record/event shapes), and utility chat titles.

Importing this package has no side effects: ``RoleClient`` only reads
``models.yml`` when constructed, not at import time; ``PromptRegistry`` only
reads a prompt file the first time it is rendered, not at construction time
(see ``prompts.py``'s module docstring); ``BedrockProvider`` only builds
its ``boto3`` client on first ``invoke``/``invoke_stream`` call, not at
construction time (see ``bedrock.py``'s module docstring); and ``loop.py``
reads the ontology only inside ``run_turn``, never at import.
"""

from .bedrock import BedrockProvider
from .loop import (
    EventSink,
    LLMRecord,
    RecordingSink,
    ToolRecord,
    TurnResult,
    run_turn,
    tool_result_digest,
)
from .prompts import (
    DEFAULT_PROMPTS_DIR,
    PromptNotFoundError,
    PromptRegistry,
    assemble_system,
    metric_definitions_block,
    negative_constraints_block,
    render_state_block,
    skill_lines_block,
)
from .roles import LLMProvider, ModelProfileError, RoleClient, RoleConfig, load_model_profiles
from .stub import StubProvider
from .titles import TITLE_MAX_CHARS, title_for
from .types import LLMResponse, ToolCall

__all__ = [
    "BedrockProvider",
    "DEFAULT_PROMPTS_DIR",
    "EventSink",
    "LLMProvider",
    "LLMRecord",
    "LLMResponse",
    "ModelProfileError",
    "PromptNotFoundError",
    "PromptRegistry",
    "RecordingSink",
    "RoleClient",
    "RoleConfig",
    "StubProvider",
    "TITLE_MAX_CHARS",
    "ToolCall",
    "ToolRecord",
    "TurnResult",
    "assemble_system",
    "load_model_profiles",
    "metric_definitions_block",
    "negative_constraints_block",
    "render_state_block",
    "run_turn",
    "skill_lines_block",
    "title_for",
    "tool_result_digest",
]
