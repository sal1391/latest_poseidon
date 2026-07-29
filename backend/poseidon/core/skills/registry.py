"""Discovery, validation and dispatch over the task/skill tree (doc 02 §2-§3).

The folder law is the registration mechanism: a directory under
``poseidon/tasks/`` holding a ``task.yml`` is a task, and each of its
``skills/<name>/`` directories holding a ``schema.py`` is a router-visible
skill whose id is ``"<task>.<skill>"``. Nothing registers itself, so there is
no decorator to forget and no import order to get right.

Three properties this module is built to hold:

**Fail-fast.** Every malformed skill raises :class:`SkillDefinitionError`
naming the offender, at discovery. A skill that is wrong is wrong at
start-up, in one legible line — never at 2am, inside a router loop, as a
truncated stack trace in a user's chat.

**Deterministic.** Task directories and skill directories are both walked in
sorted order, so ``skill_ids`` and ``tool_schemas`` are byte-identical run to
run. The router prompt is built from those schemas; an ordering that shifted
with filesystem iteration would silently change model behavior and defeat
prompt caching.

**No import-time side effects.** Discovery happens only inside
:meth:`SkillRegistry.discover`. Importing this module walks nothing and
imports no task package, which is what lets a test point discovery at a
throwaway package and lets a broken skill fail a start-up rather than every
module that mentions the registry.

Dispatch is the other half of the contract: it validates the router's
arguments against the skill's ``Args`` model and returns a structured
:class:`~poseidon.core.skills.result.SkillResult` for every outcome. It never
raises — the router loop's job is to read an error, not to survive one.

One doc-02 discovery check is deliberately absent: validating that a skill's
referenced prompts exist is deferred to Phase 5, the phase that introduces
``prompts/`` directories and the first prompt reference to check — today
there is nothing for it to validate.
"""

import importlib
import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from .context import SkillContext
from .result import SkillResult, problem

DEFAULT_TASKS_PACKAGE = "poseidon.tasks"
MANIFEST_NAME = "task.yml"
SKILLS_DIRNAME = "skills"
SCHEMA_MODULE = "schema"
SKILL_MODULE = "skill"

# The router pays for every character of every description on every turn, so
# the cap is a hard error rather than a silent truncation.
MAX_DESCRIPTION_CHARS = 300

# Bedrock's ToolName shape (bedrock-runtime service-2.json: {"type": "string",
# "max": 64, "min": 1, "pattern": "[a-zA-Z0-9_-]+"}) caps a tool name at 64
# characters -- the limit the Bedrock-safe name check below enforces on every
# registered skill id's mapped name (plan amendment aa33a2f).
_BEDROCK_TOOL_NAME_MAX_CHARS = 64


class SkillDefinitionError(Exception):
    """A task or skill package violates the folder law. Raised at discovery."""


@dataclass(frozen=True)
class RegisteredSkill:
    """One validated, router-visible skill."""

    skill_id: str
    args_model: type[BaseModel]
    fn: Callable[[SkillContext, BaseModel], SkillResult]
    description: str
    examples: list[str] = field(default_factory=list)

    @property
    def tool_schema(self) -> dict:
        """The router-facing tool definition.

        Exactly three keys: the JSON Schema comes from the ``Args`` model, so
        the thing the model is told to produce and the thing the dispatcher
        validates can never drift apart. ``examples`` stay on the registered
        skill rather than in the schema — they are prompt material for the
        router builder (phase 5), not part of the tool contract.
        """
        return {
            "name": self.skill_id,
            "description": self.description,
            "input_schema": self.args_model.model_json_schema(),
        }


class SkillRegistry:
    """The registered skills, plus the two operations the router needs."""

    def __init__(self, skills: Mapping[str, RegisteredSkill] | None = None) -> None:
        self._skills: dict[str, RegisteredSkill] = dict(skills or {})

    @classmethod
    def discover(cls, tasks_pkg: str = DEFAULT_TASKS_PACKAGE) -> "SkillRegistry":
        """Walk ``tasks_pkg`` and register every skill of every enabled task.

        ``tasks_pkg`` is a dotted package name, not a path: skill modules are
        imported through the normal import system, so their own imports
        (shared fragments, core seams) resolve exactly as they would anywhere
        else.
        """
        skills: dict[str, RegisteredSkill] = {}
        for task_name, task_dir in _task_dirs(tasks_pkg):
            if not _is_enabled(task_dir / MANIFEST_NAME, task_name):
                continue
            for skill_name, _skill_dir in _skill_dirs(task_dir):
                skill_id = f"{task_name}.{skill_name}"
                if skill_id in skills:
                    raise SkillDefinitionError(
                        f"duplicate skill id '{skill_id}': two directories under "
                        f"'{tasks_pkg}' claim it, so one would silently shadow the other"
                    )
                skills[skill_id] = _register(tasks_pkg, task_name, skill_name, skill_id)
        _check_bedrock_safe_names(skills)
        return cls(skills)

    @property
    def skill_ids(self) -> list[str]:
        return list(self._skills)

    @property
    def tool_schemas(self) -> list[dict]:
        return [skill.tool_schema for skill in self._skills.values()]

    def get(self, skill_id: str) -> RegisteredSkill:
        try:
            return self._skills[skill_id]
        except KeyError:
            raise KeyError(
                f"unknown skill '{skill_id}'; registered: {self._registered_names()}"
            ) from None

    def dispatch(self, skill_id: str, raw_args: dict, ctx: SkillContext) -> SkillResult:
        """Validate ``raw_args`` and run the skill. Never raises.

        Three failure modes, three structured results: an unknown name (404),
        arguments the ``Args`` model rejects (422), and anything the skill
        itself does wrong (500). A router loop can act on all three; an
        exception would only end the turn.

        ``Exception``, not ``BaseException``: a cancelled request or a
        ``KeyboardInterrupt`` is not a skill failure and must keep unwinding.
        """
        skill = self._skills.get(skill_id)
        if skill is None:
            return SkillResult(
                ok=False,
                error=problem(
                    404,
                    "unknown skill",
                    f"no skill is registered as '{skill_id}'; "
                    f"registered: {self._registered_names()}",
                ),
            )
        try:
            args = skill.args_model.model_validate(raw_args)
        except ValidationError as exc:
            return SkillResult(
                ok=False, error=problem(422, "invalid arguments", _explain(exc))
            )
        except Exception as exc:
            # Pydantic wraps ValueError and AssertionError raised inside a
            # validator; anything else propagates. That is a bug in the Args
            # model, not bad arguments, so it is a 500 - but it is still a
            # returned result, because "never raises" has no exceptions.
            return SkillResult(
                ok=False,
                error=problem(
                    500,
                    "skill failure",
                    f"validating arguments for '{skill_id}' raised "
                    f"{type(exc).__name__}: {exc}",
                ),
            )
        try:
            result = skill.fn(ctx, args)
        except Exception as exc:  # a skill bug is a structured failure, not a crash
            return SkillResult(
                ok=False,
                error=problem(500, "skill failure", f"{type(exc).__name__}: {exc}"),
            )
        if not isinstance(result, SkillResult):
            return SkillResult(
                ok=False,
                error=problem(
                    500,
                    "skill failure",
                    f"'{skill_id}' returned {type(result).__name__}, not a SkillResult",
                ),
            )
        return result

    def _registered_names(self) -> str:
        return ", ".join(self._skills) or "(none)"


# ---------------------------------------------------------------------------
# discovery internals
# ---------------------------------------------------------------------------


def _package_roots(tasks_pkg: str) -> list[Path]:
    """The filesystem directories ``tasks_pkg`` is made of.

    Usually one. A namespace package spread over several ``sys.path`` entries
    has several, and all of them are walked — a task contributed by a second
    root is either intentional or a name collision, and the duplicate check
    decides which.
    """
    try:
        package = importlib.import_module(tasks_pkg)
    except ImportError as exc:
        raise SkillDefinitionError(
            f"tasks package '{tasks_pkg}' is not importable: {exc}"
        ) from exc
    roots = [Path(entry) for entry in getattr(package, "__path__", [])]
    if not roots:
        raise SkillDefinitionError(
            f"'{tasks_pkg}' is a module, not a package: it has no directory to walk"
        )
    return roots


def _task_dirs(tasks_pkg: str) -> list[tuple[str, Path]]:
    """``(task name, directory)`` for every directory holding a ``task.yml``.

    Sorted by name for determinism. A directory without a manifest is not a
    task and is skipped in silence — that is what lets ``_shared/`` (and
    ``__pycache__``) live in the same tree.
    """
    found: list[tuple[str, Path]] = []
    for root in _package_roots(tasks_pkg):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir(), key=lambda path: path.name):
            if child.is_dir() and (child / MANIFEST_NAME).is_file():
                found.append((child.name, child))
    return sorted(found, key=lambda pair: pair[0])


def _is_enabled(manifest_path: Path, task_name: str) -> bool:
    """Read ``task.yml`` and decide whether the task participates at all.

    ``enabled: false`` skips the whole task before a single one of its
    modules is imported — that is what lets a task sit in the tree half-built
    (its tools written and unit-tested, its skills not yet) without breaking
    start-up.

    Only the two keys that change behavior are enforced: ``id`` (which must
    agree with the directory name, since the directory name is the task id)
    and ``enabled`` (which must be a real boolean — YAML's ``"false"`` string
    is truthy, and silently enabling a task nobody meant to enable is exactly
    the failure this fails fast on). ``title``/``description`` are metadata
    for later phases and are not required to exist.

    A manifest that is not valid YAML at all is a definition error like every
    other malformed shape, not a raw :class:`yaml.YAMLError` escaping
    discovery: fail-fast promises one legible line naming the offending file,
    and a parser traceback naming a stream position names the wrong thing.
    """
    try:
        with open(manifest_path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise SkillDefinitionError(
            f"task manifest {manifest_path} is not valid YAML: {exc}"
        ) from exc
    manifest = {} if raw is None else raw
    if not isinstance(manifest, dict):
        raise SkillDefinitionError(
            f"task '{task_name}': {MANIFEST_NAME} must be a mapping, "
            f"got {type(manifest).__name__}"
        )
    declared_id = manifest.get("id")
    if declared_id is not None and declared_id != task_name:
        raise SkillDefinitionError(
            f"task '{task_name}': {MANIFEST_NAME} declares id '{declared_id}' but the "
            f"directory is named '{task_name}' - the directory name is the task id"
        )
    enabled = manifest.get("enabled", True)
    if not isinstance(enabled, bool):
        raise SkillDefinitionError(
            f"task '{task_name}': {MANIFEST_NAME} 'enabled' must be true or false, "
            f"got {enabled!r}"
        )
    return enabled


def _skill_dirs(task_dir: Path) -> list[tuple[str, Path]]:
    """``(skill name, directory)`` for every ``skills/<name>/`` with a schema.

    Sorted by name. A skill directory without ``schema.py`` has no arguments
    to expose to the router, so it is not yet a skill — the shape a task
    takes while only its deterministic tools exist.
    """
    skills_dir = task_dir / SKILLS_DIRNAME
    if not skills_dir.is_dir():
        return []
    found = [
        (child.name, child)
        for child in skills_dir.iterdir()
        if child.is_dir() and (child / f"{SCHEMA_MODULE}.py").is_file()
    ]
    return sorted(found, key=lambda pair: pair[0])


def _check_bedrock_safe_names(skills: dict[str, "RegisteredSkill"]) -> None:
    """Every registered skill id must map INJECTIVELY onto a Bedrock tool
    name under ``"." -> "__"`` (the exact translation ``bedrock.py``'s
    request/response boundary applies -- ``_to_bedrock_tool_name``/
    ``_from_bedrock_tool_name``) and stay within Bedrock's 64-character
    ``ToolName`` cap. Checked once here, over the WHOLE final registered set,
    not at the Bedrock boundary itself (plan amendment aa33a2f): two
    DIFFERENT skill ids sharing one mapped name would be indistinguishable
    once translated, so the provider's reverse map would dispatch to
    whichever REAL skill happened to be looked up -- silently. Failing here,
    at start-up, in one legible line naming both offenders, is this module's
    own fail-fast philosophy (see the module docstring) applied to an
    invariant a DIFFERENT layer (bedrock.py) depends on -- the registry is
    where ids are minted, so this is where the guarantee belongs, not where
    it is consumed.

    Every id must also survive the ROUND TRIP on its own --
    ``reverse(forward(id)) == id`` -- which is the stronger property and the
    one ``bedrock.py`` actually relies on: a name the model returns is
    reverse-mapped and dispatched, so an id that does not come back
    unchanged would 404 on its own skill even with no other skill in the
    registry to collide with. Only an id carrying its own literal ``"__"``
    can fail it. That subsumes injectivity (a left inverse on the whole set
    implies it), so the collision check above is kept for its message rather
    than for its coverage: a colliding PAIR is better reported by naming
    both offenders than by naming one of them twice.
    """
    mapped_to_skill_id: dict[str, str] = {}
    for skill_id in skills:
        mapped = skill_id.replace(".", "__")
        if len(mapped) > _BEDROCK_TOOL_NAME_MAX_CHARS:
            raise SkillDefinitionError(
                f"skill '{skill_id}': Bedrock tool name '{mapped}' is "
                f"{len(mapped)} characters, over the "
                f"{_BEDROCK_TOOL_NAME_MAX_CHARS}-character ToolName cap"
            )
        colliding_id = mapped_to_skill_id.get(mapped)
        if colliding_id is not None:
            raise SkillDefinitionError(
                f"skill ids '{colliding_id}' and '{skill_id}' both map to "
                f"Bedrock tool name '{mapped}' under '.' -> '__' -- rename "
                "one so the provider's reverse map can tell them apart"
            )
        mapped_to_skill_id[mapped] = skill_id

    # A SECOND pass, deliberately: every colliding id also fails the round
    # trip, and running this one first would report a two-id collision one id
    # at a time. Whichever check fires, the rename it asks for is the same.
    for mapped, skill_id in mapped_to_skill_id.items():
        reversed_id = mapped.replace("__", ".")
        if reversed_id != skill_id:
            raise SkillDefinitionError(
                f"skill '{skill_id}': Bedrock tool name '{mapped}' does not map "
                f"back to it -- the provider's reverse map ('__' -> '.') returns "
                f"'{reversed_id}', which is registered as nothing, so the model's "
                "own tool call would 404. Rename the task or skill directory so "
                "the id carries no '__' of its own"
            )


def _import(module_name: str, skill_id: str) -> ModuleType:
    try:
        return importlib.import_module(module_name)
    except Exception as exc:  # a skill that cannot import is a definition error
        raise SkillDefinitionError(
            f"skill '{skill_id}': importing {module_name} failed "
            f"({type(exc).__name__}: {exc})"
        ) from exc


def _register(tasks_pkg: str, task_name: str, skill_name: str, skill_id: str) -> RegisteredSkill:
    """Import a skill's two modules and validate every part of its contract."""
    base = f"{tasks_pkg}.{task_name}.{SKILLS_DIRNAME}.{skill_name}"
    schema = _import(f"{base}.{SCHEMA_MODULE}", skill_id)
    skill = _import(f"{base}.{SKILL_MODULE}", skill_id)

    args_model = getattr(schema, "Args", None)
    if not (isinstance(args_model, type) and issubclass(args_model, BaseModel)):
        raise SkillDefinitionError(
            f"skill '{skill_id}': {SCHEMA_MODULE}.py must define `Args` as a pydantic "
            f"BaseModel subclass (got {args_model!r})"
        )

    meta = getattr(schema, "SKILL_META", None)
    if not isinstance(meta, dict):
        raise SkillDefinitionError(
            f"skill '{skill_id}': {SCHEMA_MODULE}.py must define SKILL_META as a dict "
            f"with a 'description' (got {meta!r})"
        )
    description = meta.get("description")
    if not isinstance(description, str) or not description.strip():
        raise SkillDefinitionError(
            f"skill '{skill_id}': SKILL_META['description'] must be a non-empty string "
            "- it is the only thing the router knows about this skill"
        )
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise SkillDefinitionError(
            f"skill '{skill_id}': SKILL_META['description'] is {len(description)} "
            f"characters, over the {MAX_DESCRIPTION_CHARS} cap"
        )
    examples = meta.get("examples", [])
    if not isinstance(examples, list) or not all(isinstance(x, str) for x in examples):
        raise SkillDefinitionError(
            f"skill '{skill_id}': SKILL_META['examples'] must be a list of strings "
            f"(got {examples!r})"
        )

    fn = getattr(skill, "run", None)
    if not callable(fn):
        raise SkillDefinitionError(
            f"skill '{skill_id}': {SKILL_MODULE}.py must define `run(ctx, args)` "
            f"(got {fn!r})"
        )
    _check_run_arity(fn, skill_id)

    return RegisteredSkill(
        skill_id=skill_id,
        args_model=args_model,
        fn=fn,
        description=description,
        examples=list(examples),
    )


def _check_run_arity(fn: Callable[..., Any], skill_id: str) -> None:
    """``run`` must take exactly ``(ctx, args)`` positionally.

    Checked at discovery because the dispatcher calls it positionally: a
    three-parameter ``run`` would otherwise fail on the first user question
    that reached it, as a 500 in a chat, rather than at start-up.
    """
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError) as exc:
        raise SkillDefinitionError(
            f"skill '{skill_id}': `run` signature is not inspectable ({exc})"
        ) from exc
    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if len(positional) != 2:
        raise SkillDefinitionError(
            f"skill '{skill_id}': `run` must take exactly two positional parameters "
            f"(ctx, args); got {len(positional)} in run{signature}"
        )


def _explain(exc: ValidationError) -> str:
    """Flatten a pydantic failure into one line naming every bad field.

    The router reads this to correct itself on the next turn, so it has to
    say which field and why, not "validation failed".
    """
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
        for error in exc.errors()
    )
