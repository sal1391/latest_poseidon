"""Role-to-model resolution and the stub/live provider seam (doc 03
sections 1-2).

``RoleClient`` answers two different questions that must never be
confused. :meth:`RoleClient.resolve` reports what config SAYS a role should
use -- the CONFIGURED provider/model/params, straight from ``models.yml``
plus any env override -- and stays truthful no matter what ``LLM_MODE`` is.
:meth:`RoleClient.invoke` decides what actually ANSWERS the call: in
``LLM_MODE=stub`` every role is served by the stub provider regardless of
its configured provider, which is what proves that swapping ``LLM_MODE``
changes no calling code (doc 08's validation criterion) -- callers only
ever say ``role_client.invoke(role, ...)`` and never branch on mode
themselves.

Provider availability is scoped by decision D33: Phase 5 ships
``BedrockProvider`` (Task 3) and the stub only. A role configured for
``cortex`` is fully resolvable -- ``resolve()`` reports it, ``models.yml``
declares it -- but ``invoke()`` in live mode refuses it with a pinned,
actionable error, because ``CortexProvider`` does not exist until the SPCS
deployment phase (Phase 14).
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from os import environ
from pathlib import Path
from typing import Protocol

import yaml

from poseidon.core.config import Settings
from poseidon.core.llm.types import LLMResponse

# core/llm/roles.py -> core/llm -> core -> poseidon: config/ hangs directly
# off the poseidon package (a sibling of core/, api/, tasks/), not nested
# under core/ alongside this module. Doc 03 names a repo-root config/; this
# deviates deliberately (phase-5 plan's Global Constraints, the same lesson
# P3 learned from the ontology mount) so the file ships inside the package
# and needs no extra container mount.
_POSEIDON_PACKAGE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_MODELS_PATH = _POSEIDON_PACKAGE_DIR / "config" / "models.yml"

# The registry key RoleClient.invoke consults in LLM_MODE=stub, regardless
# of which real provider the resolved RoleConfig names.
STUB_PROVIDER_KEY = "stub"

# U+2014 EM DASH, built via chr() rather than typed literally so this file
# stays pure ASCII on disk (see the Phase 4 parsing suites for the same
# convention, using an escape there instead) -- byte-pinned in
# backend/tests/test_llm_types_roles.py.
_EM_DASH = chr(0x2014)

_CORTEX_NOT_AVAILABLE = (
    "provider 'cortex' is configured but not available until the SPCS "
    "deployment phase (decision D33) " + _EM_DASH + " use profile 'bedrock' or LLM_MODE=stub"
)


class ModelProfileError(Exception):
    """``models.yml`` is missing or not valid YAML. Raised at load time."""


class LLMProvider(Protocol):
    """What every provider (Bedrock, Cortex, the stub) must answer.

    One call shape for every implementation is decision D21's point: code
    above this seam (``RoleClient.invoke``, and later the agent loop) is
    written once against this Protocol and never branches on which
    provider actually answered.
    """

    def invoke(
        self, *, system: str, messages: list[dict], tools: list[dict], model: str, params: dict
    ) -> LLMResponse: ...


@dataclass(frozen=True)
class RoleConfig:
    """One role's CONFIGURED provider/model/params -- what ``models.yml``
    (plus any env override) says, independent of what ``LLM_MODE`` actually
    invokes at call time (see the module docstring)."""

    provider: str  # "bedrock" | "cortex"
    model: str
    params: dict[str, object]  # max_tokens/temperature/enabled etc. (passthrough)


def _parse_role_config(raw: dict[str, object]) -> RoleConfig:
    """Split one ``models.yml`` role mapping into a :class:`RoleConfig`.

    Everything besides ``provider``/``model`` passes through to ``params``
    unchanged, so a new tuning knob -- or ``enabled: false``, as on
    ``supervisor`` -- needs no parser change to reach the caller.
    """
    params = {key: value for key, value in raw.items() if key not in ("provider", "model")}
    return RoleConfig(provider=raw["provider"], model=raw["model"], params=params)


def load_model_profiles(path: Path) -> dict[str, dict[str, RoleConfig]]:
    """Parse ``models.yml`` into ``{profile_name: {role_name: RoleConfig}}``.

    ``safe_load``, wrapped the same way ``skills/registry.py`` wraps every
    other manifest read in this codebase: a malformed file becomes a
    clear, named :class:`ModelProfileError` naming the path, never a raw
    ``yaml.YAMLError`` escaping with a stream position instead.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ModelProfileError(f"model profiles file {path} is not valid YAML: {exc}") from exc

    return {
        profile_name: {
            role_name: _parse_role_config(role_body)
            for role_name, role_body in profile_roles.items()
        }
        for profile_name, profile_roles in raw["profiles"].items()
    }


class RoleClient:
    """Resolves LLM roles to config and invokes them through the stub/live
    seam described in the module docstring.

    Provider instances are supplied by the caller (``providers``), never
    constructed here -- Task 1 ships no real provider, so the default is
    empty. A caller wanting ``invoke()`` to actually answer must register a
    ``"stub"`` entry (stub mode) or an entry named for the resolved role's
    provider (live mode).
    """

    def __init__(
        self, settings: Settings, providers: Mapping[str, LLMProvider] | None = None
    ) -> None:
        self._settings = settings
        self._providers: Mapping[str, LLMProvider] = {} if providers is None else providers
        models_path = (
            settings.models_path if settings.models_path is not None else DEFAULT_MODELS_PATH
        )
        self._profiles = load_model_profiles(models_path)

    def resolve(self, role: str) -> RoleConfig:
        """The CONFIGURED :class:`RoleConfig` for ``role``: the profile
        named by ``settings.llm_profile``, with any ``LLM_MODEL_<ROLE>`` /
        ``LLM_PROVIDER_<ROLE>`` env override applied.

        Reports the configured provider even in stub mode -- only
        :meth:`invoke` substitutes at call time.
        """
        profile = self._profiles[self._settings.llm_profile]
        try:
            config = profile[role]
        except KeyError:
            raise KeyError(
                f"unknown LLM role {role!r} {_EM_DASH} roles: {sorted(profile)}"
            ) from None

        role_key = role.upper()
        model_override = environ.get(f"LLM_MODEL_{role_key}")
        provider_override = environ.get(f"LLM_PROVIDER_{role_key}")
        if model_override is not None:
            config = replace(config, model=model_override)
        if provider_override is not None:
            config = replace(config, provider=provider_override)
        return config

    def invoke(
        self,
        role: str,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict] = [],  # noqa: B006 -- never mutated; pinned interface (task-1 brief)
    ) -> LLMResponse:
        """Resolve ``role`` and dispatch through the mode-selected provider.

        ``LLM_MODE=stub`` always uses the ``"stub"`` registry entry,
        regardless of the resolved provider -- the substitution IS the seam
        (see the module docstring). ``LLM_MODE=live`` uses the resolved
        provider, except ``cortex``, which raises rather than silently
        doing nothing (decision D33: not implemented until Phase 14).
        """
        config = self.resolve(role)
        if self._settings.llm_mode == "stub":
            provider_key = STUB_PROVIDER_KEY
        else:
            if config.provider == "cortex":
                raise RuntimeError(_CORTEX_NOT_AVAILABLE)
            provider_key = config.provider
        provider = self._providers[provider_key]
        return provider.invoke(
            system=system,
            messages=messages,
            tools=tools,
            model=config.model,
            params=config.params,
        )
