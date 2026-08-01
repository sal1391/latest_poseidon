import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

config = context.config

# Apply alembic.ini's [loggers] section, so `alembic upgrade head` actually
# prints the "Running upgrade 0001 -> 0002" line it otherwise computes in
# silence — that line is the compose backend's start-up evidence that the
# schema is current before the seed loader and uvicorn run.
# `disable_existing_loggers=False` keeps this from muting a host process's
# own logging when migrations are driven in-process.
#
# Phase 11 Task 2 (doc 06 section 3, the parked "alembic env.py logging"
# carryforward) -- VERIFIED, not changed: this app's own JSON logging
# regime (core/obs.py) and alembic's own plain-text logging never mix,
# because they never run in the same process. `infra/docker-compose.yml`'s
# backend command runs `python -m alembic upgrade head` as its own,
# separate `python -m` invocation, BEFORE `python -m uvicorn poseidon.api.
# app:create_app` ever starts (and therefore before create_app's own
# obs.configure_json_logging() call ever runs) -- alembic.ini's own
# [handler_console] already targets `sys.stderr` (`args = (sys.stderr,)`,
# the standard alembic template default, unchanged by this codebase), and
# a real run confirms every "Running upgrade ..." line lands on stderr,
# none on stdout (checked directly: `python -m alembic upgrade head`
# against a throwaway sqlite DB, stdout empty, stderr carrying every
# `INFO [alembic.runtime.migration] ...` line -- see this task's own
# report). `docker compose logs backend` interleaves a container's stdout
# and stderr together regardless, so alembic's own format keeps reaching
# container logs exactly as before; nothing here needed to change for that
# to remain true.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def get_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is required to run migrations")
    return url


def run_migrations_offline() -> None:
    context.configure(url=get_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(get_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
