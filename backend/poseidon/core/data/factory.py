"""The one place a DataClient is constructed from settings (spec section 9).

`api/dev_runner.py` guarded `data_backend != "synthetic"` with a structured
501; `api/internal.py` and `api/app.py` did not, and once `alembic upgrade
head` (now `drizzle-kit migrate` plus the seed's schema step) has created the
EMPTY `synthetic` schema, a Snowflake-configured deploy dispatching a data
skill answers zero rows confidently. Raising here makes every construction
site inherit the guard.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from poseidon.core.data.synthetic_client import SyntheticDataClient

if TYPE_CHECKING:
    from poseidon.core.config import Settings
    from poseidon.core.data.client import DataClient


class DataBackendUnavailable(RuntimeError):
    def __init__(self, backend: str) -> None:
        self.backend = backend
        super().__init__(
            f"data_backend={backend!r} has no adapter yet - the Snowflake client "
            "arrives Phase 19; only data_backend=synthetic is supported today"
        )


def build_data_client(settings: Settings) -> DataClient:
    if settings.data_backend == "synthetic":
        return SyntheticDataClient(settings.database_url)
    raise DataBackendUnavailable(settings.data_backend)
