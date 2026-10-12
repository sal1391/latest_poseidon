"""Phase 16 Task 3: every data client is built through one factory, so a
backend with no adapter raises loudly at construction instead of querying an
empty `synthetic` schema and answering zero rows with confidence (Phase 15
final review, R24 follow-up)."""

import pytest

from poseidon.core.config import Settings
from poseidon.core.data.factory import DataBackendUnavailable, build_data_client
from poseidon.core.data.synthetic_client import SyntheticDataClient

_DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"


def _settings(**overrides) -> Settings:
    base = dict(_env_file=None, database_url=_DSN, s3_bucket="poseidon-artifacts")
    base.update(overrides)
    return Settings(**base)


def test_synthetic_backend_builds_the_synthetic_client():
    client = build_data_client(_settings(data_backend="synthetic"))
    assert isinstance(client, SyntheticDataClient)


def test_snowflake_backend_raises_before_any_client_exists():
    with pytest.raises(DataBackendUnavailable) as excinfo:
        build_data_client(_settings(data_backend="snowflake"))
    assert excinfo.value.backend == "snowflake"
    assert "snowflake" in str(excinfo.value)
