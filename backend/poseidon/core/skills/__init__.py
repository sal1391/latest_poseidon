"""The contracts every Poseidon skill plugs into (doc 02 §2-§3).

``context``  what a skill is handed: data seam, artifact store, settings,
             conversation slots.
``result``   what it hands back: typed message parts, proof lines, RFC-7807
             problem details.
``registry`` how it is found and called: fail-fast discovery over the folder
             law, and dispatch that validates arguments and never raises.

Importing this package has no side effects: discovery is an explicit
:meth:`SkillRegistry.discover` call.
"""

from .context import ArtifactRef, ConversationSlots, SkillContext
from .registry import RegisteredSkill, SkillDefinitionError, SkillRegistry
from .result import (
    SkillResult,
    metric_grid_part,
    problem,
    table_part,
    text_part,
)

__all__ = [
    "ArtifactRef",
    "ConversationSlots",
    "RegisteredSkill",
    "SkillContext",
    "SkillDefinitionError",
    "SkillRegistry",
    "SkillResult",
    "metric_grid_part",
    "problem",
    "table_part",
    "text_part",
]
