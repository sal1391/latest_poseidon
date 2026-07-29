"""Load the deterministic synthetic dataset into the local ``synthetic`` schema.

This is the bridge between ``generate_synthetic.generate()`` (pure Python, no
database) and the Postgres tables migration ``0002`` creates. The compose
backend runs it on every start-up, between ``alembic upgrade head`` and
uvicorn, so a fresh ``docker compose up`` yields a queryable database with no
manual step — which is why the default behaviour is *skip when already
populated*, not *reload*. Restarting the backend twenty times must not
truncate and rewrite 40k rows twenty times, and must never change what a
running demo is looking at.

Idempotence is by construction: the only two paths that write are "the sales
table is empty" and "``--force`` was passed", and both begin by truncating
both tables, so the schema only ever holds a complete dataset from a single
``generate()`` call — never a partial load, never two seeds mixed together.

Determinism: the seed defaults to ``profiles.yml``'s ``seed_default``
(resolved inside ``generate()``, not duplicated here). ``--force --seed N``
reloads a different dataset; ``--force`` back to the default restores the
original one byte for byte, which the printed checksum proves.

Loading uses psycopg's ``COPY … FROM STDIN`` — one statement per table,
streaming row tuples — rather than ``executemany``: it is the fastest path
libpq offers and keeps the whole 40k-row load well inside a second, which
matters because it sits on the container's start-up critical path.

Column naming is derived from the certified ontology rather than hardcoded:
each generated row dict is keyed by the ontology column name, and
:func:`_db_columns` renders those names the same way migration 0002 created
them — ``quoted: true`` columns (``"#_FIXTURES"`` / ``"#_INQUIRIES"``) keep
their exact literal spelling, everything else is lowercased, matching what
Postgres folds an unquoted identifier to. An ontology edit that renamed a
column would therefore fail loudly here (``KeyError``) instead of silently
seeding the wrong shape.

Usage::

    DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon \\
        python -m poseidon.scripts.seed_synthetic [--force] [--seed N]
"""

import argparse
import os
import sys
from collections.abc import Iterable

import psycopg

from poseidon.core.data.synthetic_client import normalize_dsn
from poseidon.core.ontology.loader import get_ontology
from poseidon.scripts.generate_synthetic import Dataset, dataset_checksum, generate

SCHEMA = "synthetic"
SALES_ENTITY = "MARINE_SALES_PLANNING_V"
GL_ENTITY = "W_MARINE_GL_SOURCE_AI"

_MIGRATE_HINT = (
    "run `python -m alembic upgrade head` first (migration 0002 creates the "
    "`synthetic` schema)"
)

# libpq's default is "wait as long as the OS takes", which on a wrong host or a
# firewalled port means minutes of silence — unacceptable on the container's
# start-up critical path, where a misconfigured DATABASE_URL should surface as a
# fast, readable failure. `alembic upgrade head` has already proven the database
# reachable by the time this runs in compose, so this only ever bites a genuine
# misconfiguration.
CONNECT_TIMEOUT_SECONDS = 10


def _table(entity: str) -> str:
    """The Postgres table backing ``entity`` — same rule as
    ``query_builder._table_name`` on the postgres dialect."""
    return f"{SCHEMA}.{entity.lower()}"


def _db_columns(entity: str, column_names: Iterable[str]) -> list[str]:
    """Render ontology column names as migration-0002 identifiers."""
    columns = get_ontology().entity(entity).columns
    return [
        f'"{columns[name].name}"' if columns[name].quoted else name.lower()
        for name in column_names
    ]


def _copy_rows(conn: psycopg.Connection, entity: str, rows: list[dict]) -> None:
    """Stream ``rows`` into ``entity``'s table with a single COPY statement.

    Every row dict is built by the same generator code path, so they all share
    one key order; that order (taken from the first row) fixes both the COPY
    column list and the value tuples, so the two can never drift apart.
    """
    if not rows:
        return
    keys = list(rows[0])
    column_list = ", ".join(_db_columns(entity, keys))
    with conn.cursor() as cur:
        with cur.copy(f"COPY {_table(entity)} ({column_list}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(tuple(row[key] for key in keys))


def _row_counts(conn: psycopg.Connection) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {_table(SALES_ENTITY)}")
        sales = cur.fetchone()[0]
        cur.execute(f"SELECT COUNT(*) FROM {_table(GL_ENTITY)}")
        gl = cur.fetchone()[0]
    return sales, gl


def _truncate(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE {_table(SALES_ENTITY)}, {_table(GL_ENTITY)}")


def _load(conn: psycopg.Connection, dataset: Dataset) -> None:
    _truncate(conn)
    _copy_rows(conn, SALES_ENTITY, dataset.sales_rows)
    _copy_rows(conn, GL_ENTITY, dataset.gl_rows)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m poseidon.scripts.seed_synthetic",
        description="Seed the local `synthetic` Postgres schema with the "
        "deterministic synthetic dataset. Skips when already populated.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="truncate both tables and reload even if they already hold rows",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="generator seed (default: profiles.yml's seed_default)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    url = os.environ.get("DATABASE_URL", "")
    if not url.strip():
        print(
            "DATABASE_URL is required to seed the synthetic schema "
            "(e.g. postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon)",
            file=sys.stderr,
        )
        return 2

    try:
        with psycopg.connect(
            normalize_dsn(url), connect_timeout=CONNECT_TIMEOUT_SECONDS
        ) as conn:
            try:
                sales_rows, gl_rows = _row_counts(conn)
            except psycopg.errors.UndefinedTable:
                # ASCII only: this lands on a console that may be Windows
                # cp1252, where an em dash prints as "?".
                print(
                    f"the {SCHEMA} schema does not exist - {_MIGRATE_HINT}",
                    file=sys.stderr,
                )
                return 2

            if sales_rows and not args.force:
                print(
                    f"already seeded ({sales_rows} sales rows / {gl_rows} GL rows); "
                    "use --force to truncate and reload"
                )
                return 0

            dataset = generate(seed=args.seed)
            _load(conn, dataset)
            # Built here (where the data is), printed below — only once the
            # `with` block has committed, so "seeded" is never claimed for a
            # load that then failed to land.
            summary = (
                f"seeded synthetic: {len(dataset.sales_rows)} sales rows / "
                f"{len(dataset.gl_rows)} GL rows, checksum {dataset_checksum(dataset)}"
            )
    except psycopg.OperationalError as exc:
        print(f"cannot reach DATABASE_URL: {exc}".strip(), file=sys.stderr)
        return 2

    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
