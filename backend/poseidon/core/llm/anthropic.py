"""AnthropicProvider: the direct Claude Messages API over httpx (Phase 17
PR C, spec 9.1, D68, D79).

Every translation rule -- request body, block mapping, tool-name wire
spelling, response normalization, the error surface -- lives in
``messages_api.py``, shared with the Cortex provider. This module supplies
only what is Anthropic-specific: ``error_prefix``, the endpoint
(``<base_url>/v1/messages``) and the three request headers.

Credentials are resolved lazily, at ``invoke`` time, never in the
constructor -- the same reason ``bedrock.py`` defers ``boto3.client``:
registries build providers unconditionally in offline tests and stub-mode
boots, where no key exists and none is needed. The key is the constructor's
``api_key`` (wired from ``Settings.anthropic_api_key``), else the
``ANTHROPIC_API_KEY`` environment variable; with neither, ``_headers`` raises
:class:`ProviderCredentialsError` with :data:`NO_API_KEY_MESSAGE` before any
request is sent. The key is only ever placed in the ``x-api-key`` header; it
never reaches an error ``text``.
"""

import os

import httpx

from poseidon.core.llm.messages_api import (
    ANTHROPIC_VERSION,
    MessagesAPIProvider,
    ProviderCredentialsError,
)

DEFAULT_BASE_URL = "https://api.anthropic.com"

NO_API_KEY_MESSAGE = (
    "anthropic provider has no API key: set ANTHROPIC_API_KEY (Settings.anthropic_api_key) "
    "or use profile 'bedrock' or LLM_MODE=stub"
)


class AnthropicProvider(MessagesAPIProvider):
    """The ``LLMProvider`` for the direct Anthropic API. See the module
    docstring; everything else is :class:`MessagesAPIProvider`."""

    error_prefix = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(client=client)
        self._api_key = api_key
        self._base_url = base_url

    def _endpoint(self) -> str:
        return f"{self._base_url.rstrip('/')}/v1/messages"

    def _headers(self) -> dict[str, str]:
        key = self._api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderCredentialsError(NO_API_KEY_MESSAGE)
        return {
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
