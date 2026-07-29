"""Contract tests for the skill framework: discovery, schema/dispatch parity,
and the two failure surfaces the framework promises.

Two kinds of task package are exercised here.

*The real one* — ``poseidon.tasks`` — proves the repo's own folder law is
honored end to end: exactly one router-visible skill exists today
(``data_qa.metric_query``), its JSON schema is generated from its ``Args``
model, and dispatching it goes through argument validation first.

*Throwaway ones* — built under ``tmp_path`` by the ``tasks_package`` fixture —
prove the fail-fast rules. A permanently broken skill cannot live in the repo
tree (discovery would crash the whole suite, which is exactly the point), so
each malformed shape is materialized as real files in a real importable
package, discovered, and asserted on.

Nothing in this module touches a database: argument validation and the
not-implemented stub both return before any query runs, and
:class:`_NullDataClient` fails loudly if that ever stops being true.
"""

import importlib
import sys
import textwrap
from collections.abc import Callable
from typing import Any

import pytest

from poseidon.core.config import Settings
from poseidon.core.data.client import BreakdownResult, MetricResult, PeriodRange
from poseidon.core.skills.context import ConversationSlots, SkillContext
from poseidon.core.skills.registry import SkillDefinitionError, SkillRegistry

# Present and non-blank is all Settings asks of it; nothing here connects.
PLACEHOLDER_DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"


class _NullDataClient:
    """A ``DataClient`` stand-in whose every method fails loudly.

    Registry tests stop at argument validation or at the not-implemented
    stub, so none of them should reach the data layer. If one does, that is a
    bug in the test — not a reason to require a running Postgres.
    """

    _MESSAGE = (
        "the registry tests must never reach the data layer: argument "
        "validation and the not-implemented stub both return before any query"
    )

    def list_dimension_values(self, *args: object, **kwargs: object) -> list[str]:
        raise AssertionError(self._MESSAGE)

    def available_periods(self, *args: object, **kwargs: object) -> PeriodRange:
        raise AssertionError(self._MESSAGE)

    def run_metric_query(self, *args: object, **kwargs: object) -> MetricResult:
        raise AssertionError(self._MESSAGE)

    def run_breakdown_query(self, *args: object, **kwargs: object) -> BreakdownResult:
        raise AssertionError(self._MESSAGE)


def _ctx(**slots: Any) -> SkillContext:
    """The context every dispatch test passes in.

    ``artifacts`` is None (no skill in this phase writes one) and ``data`` is
    the null client above, so the context is complete without any external
    dependency.
    """
    return SkillContext(
        data=_NullDataClient(),
        artifacts=None,
        settings=Settings(
            _env_file=None, database_url=PLACEHOLDER_DSN, s3_bucket="poseidon-artifacts"
        ),
        state=ConversationSlots(**slots),
    )


# ---------------------------------------------------------------------------
# throwaway task packages (the fail-fast fixtures)
# ---------------------------------------------------------------------------

_TASK_YML = """
    id: {task}
    title: Throwaway task
    description: Exists only inside a test's tmp_path.
    enabled: {enabled}
"""

_GOOD_SCHEMA = """
    from pydantic import BaseModel


    class Args(BaseModel):
        q: str


    SKILL_META = {
        "description": "Throwaway skill used by the registry contract tests.",
        "examples": ["say hello"],
    }
"""

_GOOD_SKILL = """
    from poseidon.core.skills.result import SkillResult, text_part


    def run(ctx, args):
        return SkillResult(ok=True, parts=[text_part(args.q)])
"""


def _files(
    task: str,
    skill: str,
    *,
    schema_py: str = _GOOD_SCHEMA,
    skill_py: str = _GOOD_SKILL,
    enabled: bool = True,
    with_root_init: bool = True,
) -> dict[str, str]:
    """One task holding one skill, laid out per the folder law of doc 02 §1.

    ``with_root_init=False`` leaves the package root without an
    ``__init__.py`` so it resolves as an implicit namespace package — the only
    way a single task name can be contributed by two different sys.path roots.
    """
    files = {
        f"{task}/task.yml": _TASK_YML.format(task=task, enabled=str(enabled).lower()),
        f"{task}/__init__.py": "",
        f"{task}/skills/__init__.py": "",
        f"{task}/skills/{skill}/__init__.py": "",
        f"{task}/skills/{skill}/schema.py": schema_py,
        f"{task}/skills/{skill}/skill.py": skill_py,
    }
    if with_root_init:
        files["__init__.py"] = ""
    return files


@pytest.fixture
def tasks_package(tmp_path, monkeypatch) -> Any:
    """Materialize an importable throwaway tasks package and hand back its name.

    The returned factory writes ``files`` (relative path -> source) under
    ``tmp_path/<root>/<name>/``, puts ``tmp_path/<root>`` on ``sys.path``, and
    returns ``name`` for :meth:`SkillRegistry.discover`. Distinct ``root``
    values let one package name be contributed by two path entries.

    Teardown drops every module imported from the package: pytest restores
    ``sys.path``, but a stale ``sys.modules`` entry would otherwise shadow a
    later test that reuses the same package name.
    """
    created: list[str] = []

    def make(name: str, files: dict[str, str], root: str = "pkgroot") -> str:
        base = tmp_path / root / name
        for relative, body in files.items():
            path = base / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
        monkeypatch.syspath_prepend(str(tmp_path / root))
        importlib.invalidate_caches()
        created.append(name)
        return name

    yield make

    for name in created:
        for module in [m for m in sys.modules if m == name or m.startswith(f"{name}.")]:
            del sys.modules[module]


# ---------------------------------------------------------------------------
# the real repo tree
# ---------------------------------------------------------------------------


def test_discovery_finds_metric_query_only():
    reg = SkillRegistry.discover()
    assert reg.skill_ids == ["data_qa.metric_query"]  # customer_insight is disabled


def test_schema_dispatch_parity():
    reg = SkillRegistry.discover()
    schema_names = {s["name"] for s in reg.tool_schemas}
    assert schema_names == set(reg.skill_ids)
    for s in reg.tool_schemas:
        assert s["description"] and len(s["description"]) <= 300
        assert s["input_schema"]["type"] == "object"


def test_metric_query_router_facing_fields_carry_descriptions():
    """The three fields a router gets wrong without help — which dimension a
    breakdown is over, that a comparison is a SECOND window, and how large a
    breakdown may be — must describe themselves in the generated schema, not
    only in a Python comment the model never sees. ``group_by`` and
    ``compare_period`` additionally state their mutual exclusion, because the
    ``Args`` validator rejects the combination as a 422 and the cheapest place
    to prevent that round trip is the tool definition itself."""
    reg = SkillRegistry.discover()
    schema = next(s for s in reg.tool_schemas if s["name"] == "data_qa.metric_query")
    properties = schema["input_schema"]["properties"]

    assert properties["group_by"]["description"] == (
        "Certified dimension column to break down by (e.g. CUST_NM, LOC_NM). "
        "Cannot be combined with compare_period."
    )
    assert properties["compare_period"]["description"] == (
        "Second window for side-by-side comparison. Cannot be combined with group_by."
    )
    assert properties["top_n"]["description"] == "Row limit for breakdowns, 1-50."


def test_dispatch_validates_args_structurally():
    reg = SkillRegistry.discover()
    res = reg.dispatch("data_qa.metric_query", {"nonsense": True}, _ctx())
    assert res.ok is False and res.error["status"] == 422
    # the detail names the fields the router got wrong, not a bare "invalid"
    assert "metrics" in res.error["detail"] and "period" in res.error["detail"]


def test_dispatch_unknown_skill_is_structured():
    reg = SkillRegistry.discover()
    res = reg.dispatch("no.such_skill", {}, _ctx())
    assert res.ok is False and res.error["status"] == 404


def test_get_unknown_skill_raises_with_a_useful_message():
    reg = SkillRegistry.discover()
    with pytest.raises(KeyError) as err:
        reg.get("no.such_skill")
    assert "no.such_skill" in str(err.value)
    assert "data_qa.metric_query" in str(err.value)  # lists what IS registered


def test_importing_the_registry_does_not_discover_anything():
    """Discovery is an explicit call, never an import side effect: a broken
    skill must fail a start-up (or a test), not every module that imports the
    registry — and a test must be able to point discovery somewhere else."""
    module = importlib.import_module("poseidon.core.skills.registry")
    assert not [
        name for name, value in vars(module).items() if isinstance(value, SkillRegistry)
    ]
    assert SkillRegistry().skill_ids == []


# ---------------------------------------------------------------------------
# fail-fast discovery (throwaway packages)
# ---------------------------------------------------------------------------


def test_broken_skill_fails_discovery_loudly(tasks_package: Callable[..., str]):
    """A skill with no SKILL_META crashes discovery, naming the offender."""
    schema_without_meta = """
        from pydantic import BaseModel


        class Args(BaseModel):
            q: str
    """
    pkg = tasks_package(
        "broken_tasks", _files("demo_task", "no_meta", schema_py=schema_without_meta)
    )

    with pytest.raises(SkillDefinitionError) as err:
        SkillRegistry.discover(pkg)

    assert "demo_task.no_meta" in str(err.value)
    assert "SKILL_META" in str(err.value)


def test_args_must_be_a_pydantic_model(tasks_package: Callable[..., str]):
    schema_with_plain_class = """
        class Args:
            q: str


        SKILL_META = {"description": "Throwaway.", "examples": []}
    """
    pkg = tasks_package(
        "plain_args_tasks",
        _files("demo_task", "plain_args", schema_py=schema_with_plain_class),
    )

    with pytest.raises(SkillDefinitionError) as err:
        SkillRegistry.discover(pkg)

    assert "demo_task.plain_args" in str(err.value)
    assert "Args" in str(err.value)


def test_blank_description_fails_discovery(tasks_package: Callable[..., str]):
    schema_with_blank_description = """
        from pydantic import BaseModel


        class Args(BaseModel):
            q: str


        SKILL_META = {"description": "   ", "examples": []}
    """
    pkg = tasks_package(
        "blank_tasks",
        _files("demo_task", "blank_desc", schema_py=schema_with_blank_description),
    )

    with pytest.raises(SkillDefinitionError) as err:
        SkillRegistry.discover(pkg)

    assert "demo_task.blank_desc" in str(err.value)


def test_overlong_description_fails_discovery(tasks_package: Callable[..., str]):
    """The router pays for every character of every description, so the cap is
    a hard error rather than a truncation."""
    schema_with_long_description = f"""
        from pydantic import BaseModel


        class Args(BaseModel):
            q: str


        SKILL_META = {{"description": "{"x" * 301}", "examples": []}}
    """
    pkg = tasks_package(
        "long_tasks",
        _files("demo_task", "long_desc", schema_py=schema_with_long_description),
    )

    with pytest.raises(SkillDefinitionError) as err:
        SkillRegistry.discover(pkg)

    assert "demo_task.long_desc" in str(err.value)
    assert "301" in str(err.value)


def test_run_must_take_ctx_and_args(tasks_package: Callable[..., str]):
    skill_with_wrong_arity = """
        from poseidon.core.skills.result import SkillResult


        def run(ctx):
            return SkillResult(ok=True)
    """
    pkg = tasks_package(
        "arity_tasks", _files("demo_task", "bad_arity", skill_py=skill_with_wrong_arity)
    )

    with pytest.raises(SkillDefinitionError) as err:
        SkillRegistry.discover(pkg)

    assert "demo_task.bad_arity" in str(err.value)
    assert "run" in str(err.value)


def test_missing_run_fails_discovery(tasks_package: Callable[..., str]):
    pkg = tasks_package(
        "norun_tasks", _files("demo_task", "no_run", skill_py="X = 1\n")
    )

    with pytest.raises(SkillDefinitionError) as err:
        SkillRegistry.discover(pkg)

    assert "demo_task.no_run" in str(err.value)
    assert "run" in str(err.value)


def test_duplicate_skill_id_fails_discovery(tasks_package: Callable[..., str]):
    """Two sys.path roots contributing the same task name (a namespace-package
    layout) must collide loudly instead of one silently winning."""
    files = _files("demo_task", "twice", with_root_init=False)
    tasks_package("dup_tasks", files, root="root_a")
    pkg = tasks_package("dup_tasks", files, root="root_b")

    with pytest.raises(SkillDefinitionError) as err:
        SkillRegistry.discover(pkg)

    assert "demo_task.twice" in str(err.value)
    assert "duplicate" in str(err.value).lower()


def test_malformed_task_manifest_fails_discovery_as_a_definition_error(
    tasks_package: Callable[..., str],
):
    """A ``task.yml`` that is not valid YAML must surface as the framework's
    own :class:`SkillDefinitionError` naming the file — not as a raw
    ``yaml.ScannerError``/``ParserError`` escaping discovery, which would
    report a stream position instead of the offending manifest and break the
    "one legible line at start-up" promise every other malformed shape keeps.
    """
    files = _files("demo_task", "hello")
    # An unterminated double-quoted scalar: the scanner reads to EOF looking
    # for the closing quote and raises before a mapping is ever built.
    files["demo_task/task.yml"] = """
        id: demo_task
        title: "unterminated
        enabled: true
    """
    pkg = tasks_package("badyaml_tasks", files)

    with pytest.raises(SkillDefinitionError) as err:
        SkillRegistry.discover(pkg)

    assert "task.yml" in str(err.value)
    assert "not valid YAML" in str(err.value)


def test_disabled_task_is_never_imported(tasks_package: Callable[..., str]):
    """``enabled: false`` skips the whole task — the skill module is not even
    imported, which is what lets a Phase-8 task sit in the tree half-built."""
    exploding_skill = """
        raise RuntimeError("a disabled task must never be imported")
    """
    pkg = tasks_package(
        "disabled_tasks",
        _files("demo_task", "unfinished", skill_py=exploding_skill, enabled=False),
    )

    reg = SkillRegistry.discover(pkg)

    assert reg.skill_ids == []
    assert reg.tool_schemas == []


def test_directories_without_a_manifest_are_not_tasks(tasks_package: Callable[..., str]):
    """``_shared/`` (and anything else without a task.yml) is skipped."""
    files = _files("demo_task", "hello")
    files["_shared/__init__.py"] = ""
    files["_shared/fragments.py"] = "VALUE = 1\n"
    pkg = tasks_package("shared_tasks", files)

    assert SkillRegistry.discover(pkg).skill_ids == ["demo_task.hello"]


def test_skill_ids_and_schemas_are_sorted_deterministically(
    tasks_package: Callable[..., str],
):
    """Ordering is alphabetical by (task, skill), never filesystem order — the
    router prompt must be byte-identical run to run."""
    files = {
        **_files("zeta_task", "second"),
        **_files("alpha_task", "second"),
        **_files("alpha_task", "first"),
    }
    pkg = tasks_package("ordered_tasks", files)

    reg = SkillRegistry.discover(pkg)

    assert reg.skill_ids == ["alpha_task.first", "alpha_task.second", "zeta_task.second"]
    assert [s["name"] for s in reg.tool_schemas] == reg.skill_ids


# ---------------------------------------------------------------------------
# dispatch (throwaway packages)
# ---------------------------------------------------------------------------


def test_dispatch_runs_a_valid_skill(tasks_package: Callable[..., str]):
    pkg = tasks_package("happy_tasks", _files("demo_task", "hello"))
    reg = SkillRegistry.discover(pkg)

    res = reg.dispatch("demo_task.hello", {"q": "hi"}, _ctx())

    assert res.ok is True and res.error is None
    assert res.parts == [{"kind": "text", "payload": {"markdown": "hi"}}]


def test_dispatch_survives_a_broken_argument_validator(tasks_package: Callable[..., str]):
    """Pydantic wraps ValueError and AssertionError raised inside a validator;
    anything else propagates. The dispatcher's promise is unconditional, so a
    buggy validator is a 500, not an exception in the router loop."""
    schema_with_broken_validator = """
        from pydantic import BaseModel, model_validator


        class Args(BaseModel):
            q: str

            @model_validator(mode="after")
            def _explode(self):
                raise TypeError("validator bug")


        SKILL_META = {"description": "Throwaway.", "examples": []}
    """
    pkg = tasks_package(
        "badvalidator_tasks",
        _files("demo_task", "bad_validator", schema_py=schema_with_broken_validator),
    )
    reg = SkillRegistry.discover(pkg)

    res = reg.dispatch("demo_task.bad_validator", {"q": "hi"}, _ctx())

    assert res.ok is False and res.error["status"] == 500
    assert "validator bug" in res.error["detail"]


def test_dispatch_never_lets_a_skill_exception_escape(tasks_package: Callable[..., str]):
    exploding_skill = """
        def run(ctx, args):
            raise ZeroDivisionError("boom")
    """
    pkg = tasks_package(
        "boom_tasks", _files("demo_task", "boom", skill_py=exploding_skill)
    )
    reg = SkillRegistry.discover(pkg)

    res = reg.dispatch("demo_task.boom", {"q": "hi"}, _ctx())

    assert res.ok is False and res.error["status"] == 500
    assert "boom" in res.error["detail"]


def test_dispatch_rejects_a_skill_that_returns_the_wrong_type(
    tasks_package: Callable[..., str],
):
    """The router loop unpacks ``parts``/``proof``; a skill returning anything
    else is a definition bug that must surface as a structured failure."""
    wrong_return = """
        def run(ctx, args):
            return {"ok": True}
    """
    pkg = tasks_package(
        "wrongtype_tasks", _files("demo_task", "wrong", skill_py=wrong_return)
    )
    reg = SkillRegistry.discover(pkg)

    res = reg.dispatch("demo_task.wrong", {"q": "hi"}, _ctx())

    assert res.ok is False and res.error["status"] == 500
    assert "SkillResult" in res.error["detail"]
