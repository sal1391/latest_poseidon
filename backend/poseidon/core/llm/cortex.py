"""CortexProvider: Snowflake Cortex's Anthropic-compatible Messages endpoint
over httpx (Phase 17 PR C, spec 9.2, D40, D68, D79).

Every translation rule lives in ``messages_api.py``, shared with the direct
Anthropic provider. This module supplies only what is Cortex-specific:
``error_prefix``, the endpoint and the request headers.

Sources, read on 2026-10-23 and never written from memory:

- Snowflake "Cortex LLM REST API", Messages API section: ``POST
  https://<account_identifier>.snowflakecomputing.com/api/v2/cortex/v1/messages``;
  ``anthropic-version: 2023-06-01`` is required; ``Authorization: Bearer
  <JWT | OAuth | PAT>``; optional ``X-Snowflake-Authorization-Token-Type``
  (``KEYPAIR_JWT`` | ``OAUTH`` | ``PROGRAMMATIC_ACCESS_TOKEN``); Claude models
  only; ``service_tier`` unsupported. Snowflake-generated errors: 400
  invalid_request_error / unknown model, 402 budget exceeded, 403 Not
  Authorized, 429 too many requests, 503 inference timed out.
- Snowflake "Using Snowflake-provided service user credentials" (SPCS): the
  token file ``/snowflake/session/token`` and the ``SNOWFLAKE_HOST`` /
  ``SNOWFLAKE_ACCOUNT`` environment variables. The file is refreshed every few
  minutes, each token is valid for up to one hour, and it is usable only from
  inside SPCS.

Resolution is lazy (at ``invoke`` time, never in the constructor), for the
reason ``anthropic.py`` gives. The account URL is the constructor's
``account_url``, else ``SNOWFLAKE_ACCOUNT_URL``, else ``https://{SNOWFLAKE_HOST}``;
with none, :data:`NO_ACCOUNT_MESSAGE`. The token is the constructor's
``token``, else ``SNOWFLAKE_CORTEX_TOKEN``, else the token file's stripped
contents; with none, :data:`NO_TOKEN_MESSAGE`. The token is resolved on EVERY
call and never cached: the SPCS file is rewritten under us. The token type is
the constructor's ``token_type``, else ``SNOWFLAKE_CORTEX_TOKEN_TYPE``, else
``OAUTH`` when the token came from the file, else the header is omitted. The
token is only ever placed in the ``Authorization`` header; it never reaches an
error ``text``.
"""

import os
from pathlib import Path

import httpx

from poseidon.core.llm.messages_api import (
    ANTHROPIC_VERSION,
    MessagesAPIProvider,
    ProviderCredentialsError,
)

DEFAULT_TOKEN_FILE = "/snowflake/session/token"

NO_ACCOUNT_MESSAGE = (
    "cortex provider has no account URL: set SNOWFLAKE_ACCOUNT_URL "
    "(https://<account_identifier>.snowflakecomputing.com) or run inside SPCS where "
    "SNOWFLAKE_HOST is set"
)
NO_TOKEN_MESSAGE = (
    "cortex provider has no token: set SNOWFLAKE_CORTEX_TOKEN (a programmatic access token "
    "or key-pair JWT) or run inside SPCS where /snowflake/session/token exists"
)


class CortexProvider(MessagesAPIProvider):
    """The ``LLMProvider`` for Snowflake Cortex. See the module docstring;
    everything else is :class:`MessagesAPIProvider`."""

    error_prefix = "cortex"

    def __init__(
        self,
        account_url: str | None = None,
        token: str | None = None,
        token_type: str | None = None,
        token_file: str | Path = DEFAULT_TOKEN_FILE,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(client=client)
        self._account_url = account_url
        self._token = token
        self._token_type = token_type
        self._token_file = token_file

    def _endpoint(self) -> str:
        host = os.environ.get("SNOWFLAKE_HOST")
        account_url = (
            self._account_url
            or os.environ.get("SNOWFLAKE_ACCOUNT_URL")
            or (f"https://{host}" if host else None)
        )
        if not account_url:
            raise ProviderCredentialsError(NO_ACCOUNT_MESSAGE)
        return f"{account_url.rstrip('/')}/api/v2/cortex/v1/messages"

    def _headers(self) -> dict[str, str]:
        token = self._token or os.environ.get("SNOWFLAKE_CORTEX_TOKEN")
        from_file = False
        if not token and Path(self._token_file).is_file():
            token = Path(self._token_file).read_text(encoding="utf-8").strip()
            from_file = True
        if not token:
            raise ProviderCredentialsError(NO_TOKEN_MESSAGE)
        token_type = self._token_type or os.environ.get("SNOWFLAKE_CORTEX_TOKEN_TYPE")
        if not token_type and from_file:
            token_type = "OAUTH"
        headers = {
            "Authorization": f"Bearer {token}",
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        if token_type:
            headers["X-Snowflake-Authorization-Token-Type"] = token_type
        return headers
