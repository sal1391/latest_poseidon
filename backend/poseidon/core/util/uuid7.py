"""RFC 9562 UUIDv7: a hand-rolled, injectable-clock generator.

Why hand-rolled: the standard library's ``uuid`` module ships only
uuid1/3/4/5 generators through Python 3.13 -- ``uuid.uuid7()`` only lands in
3.14 -- and this project's deploy image pins Python 3.12 (see the repo root
``Dockerfile``), well below that floor, regardless of what a local dev venv
happens to run. Migration 0003's own docstring already named this exact gap
as the reason ``RunLogWriter`` fell back to ``uuid4()`` ids, deferring a
real fix to "Phase 10 ... when durable conversations/messages land and id
time-ordering starts to matter for insert locality." This module is that
fix: ``conversations``/``messages`` (migration 0004) are exactly the "busy
table" case doc 05 section 6 calls out ("UUIDv7 keys throughout -- insert
locality on the hot tables"), so their ids are minted here instead.

Layout (RFC 9562 section 5.2, 128 bits total, most significant bit first):

    48 bits  unix_ts_ms   -- milliseconds since the Unix epoch
     4 bits  ver          -- 0b0111 (7)
    12 bits  rand_a       -- random
     2 bits  var          -- 0b10 (the RFC 4122 variant)
    62 bits  rand_b       -- random

``now_ms`` is an injectable parameter (default: the real clock) purely so
tests can pin the timestamp half and assert time-ordering/round-tripping
without sleeping between calls -- every production call site omits it.
Only the timestamp is deterministic given ``now_ms``; the 74 random bits
(``rand_a``/``rand_b``) come from :mod:`secrets`, so two ids minted in the
same millisecond are still (overwhelmingly, 1-in-2**74) distinct. This is an
id generator whose output ends up in URLs and database primary keys, not a
test fixture, so it reaches for the same cryptographic-quality randomness
Postgres's own ``gen_random_uuid()`` uses rather than :mod:`random`'s
faster, non-cryptographic PRNG.
"""

import secrets
import time
import uuid

_VERSION = 0x7
_VARIANT = 0b10

_UNIX_TS_MS_BITS = 48
_RAND_A_BITS = 12
_RAND_B_BITS = 62

# Masks/shifts derived from the bit widths above; kept as named constants
# (rather than inlined magic numbers in `uuid7`) so the RFC 9562 layout in
# the module docstring and the arithmetic that implements it stay visibly
# in sync.
_UNIX_TS_MS_MASK = (1 << _UNIX_TS_MS_BITS) - 1
_VERSION_SHIFT = 128 - _UNIX_TS_MS_BITS - 4  # 76: version nibble starts here
_UNIX_TS_MS_SHIFT = _VERSION_SHIFT + 4  # 80: timestamp starts here
_RAND_A_SHIFT = _VERSION_SHIFT - _RAND_A_BITS  # 64: rand_a starts here
_VARIANT_SHIFT = _RAND_A_SHIFT - 2  # 62: the 2 variant bits start here


def uuid7(now_ms: int | None = None) -> uuid.UUID:
    """Mint one UUIDv7. ``now_ms`` overrides the clock (milliseconds since
    the Unix epoch) for deterministic tests; production callers always omit
    it, taking the real current time instead. See the module docstring for
    the exact bit layout."""
    if now_ms is None:
        now_ms = time.time_ns() // 1_000_000

    ts_bits = now_ms & _UNIX_TS_MS_MASK
    rand_a = secrets.randbits(_RAND_A_BITS)
    rand_b = secrets.randbits(_RAND_B_BITS)

    value = (
        (ts_bits << _UNIX_TS_MS_SHIFT)
        | (_VERSION << _VERSION_SHIFT)
        | (rand_a << _RAND_A_SHIFT)
        | (_VARIANT << _VARIANT_SHIFT)
        | rand_b
    )
    return uuid.UUID(int=value)


__all__ = ["uuid7"]
