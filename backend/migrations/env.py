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
