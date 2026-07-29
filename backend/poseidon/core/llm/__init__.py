"""The config-driven LLM provider layer (doc 03): normalized types, the
model-profile-to-role resolver, the stub/live provider seam, and the prompt
registry/assembly layer.

Phase 5 builds this package task by task; this ``__init__`` re-exports
whatever each task adds so a caller never needs to know which submodule a
name lives in. As of Task 2: the normalized response types, the role
client, the stub provider, and the prompt registry/assembly functions. The
Bedrock provider (Task 3) and the agent loop's ``run_turn`` (Task 4) join
this list as their tasks land.

Importing this package has no side effects: ``RoleClient`` only reads
``models.yml`` when constructed, not at import time, and ``PromptRegistry``
only reads a prompt file the first time it is rendered, not at construction
time (see ``prompts.py``'s module docstring).
"""

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
from .types import LLMResponse, ToolCall

__all__ = [
    "DEFAULT_PROMPTS_DIR",
    "LLMProvider",
    "LLMResponse",
    "ModelProfileError",
    "PromptNotFoundError",
    "PromptRegistry",
    "RoleClient",
    "RoleConfig",
    "StubProvider",
    "ToolCall",
    "assemble_system",
    "load_model_profiles",
    "metric_definitions_block",
    "negative_constraints_block",
    "render_state_block",
    "skill_lines_block",
]
