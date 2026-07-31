"""Tests for :mod:`poseidon.core.util.uuid7` (Phase 10 Task 1).

Doc 05 section 6 pins "UUIDv7 keys throughout (insert locality on the hot
tables)" for ``conversations``/``messages`` (migration 0004); this module is
the pure-Python RFC 9562 generator that makes good on that, since the
standard library's ``uuid`` module ships no ``uuid7`` for the Python
versions this project targets (see the module's own docstring). Every test
below pins one piece of the RFC 9562 layout directly, rather than trusting
the implementation's own constants -- a version/variant/timestamp bug in
``uuid7.py`` would corrupt every id migration 0004's tables ever store.
"""

import uuid
from pathlib import Path

from poseidon.core.util import uuid7 as uuid7_module
from poseidon.core.util.uuid7 import uuid7


def test_version_nibble_is_7():
    """Bits 76-79 (the ``version`` nibble) must read 7 -- ``uuid.UUID``'s own
    ``.version`` property decodes exactly that nibble, so this is a direct
    check of RFC 9562's version field, not an indirect one."""
    generated = uuid7(now_ms=0)

    assert generated.version == 7


def test_variant_bits_are_rfc_4122():
    """The top two bits of byte 8 (bits 62-63) must read ``0b10`` -- the RFC
    4122 variant. ``uuid.UUID.variant`` decodes precisely those two bits."""
    generated = uuid7(now_ms=0)

    assert generated.variant == uuid.RFC_4122


def test_time_ordering_across_now_ms_values():
    """The 48-bit timestamp occupies the top bits of the 128-bit integer, so
    a later ``now_ms`` must always sort as a larger integer -- the whole
    point of choosing UUIDv7 for insert locality on a busy table."""
    earlier = uuid7(now_ms=1)
    later = uuid7(now_ms=2)

    assert earlier.int < later.int


def test_same_now_ms_produces_distinct_ids():
    """Two ids minted in the same millisecond must still differ -- the 74
    remaining bits (rand_a/rand_b) are randomly generated on every call, not
    derived solely from the timestamp."""
    first = uuid7(now_ms=1_000)
    second = uuid7(now_ms=1_000)

    assert first != second


def test_timestamp_round_trips_through_the_top_48_bits():
    """The top 48 bits of the 128-bit integer must equal ``now_ms`` exactly
    -- the round trip the whole "insert locality" claim depends on."""
    now_ms = 1_732_000_000_123

    generated = uuid7(now_ms=now_ms)

    assert (generated.int >> 80) == now_ms


def test_uuid7_module_is_ascii_on_disk():
    """Matches the codebase-wide ASCII-on-disk convention (e.g.
    ``test_runlog_module_is_ascii_on_disk``) -- a bit-layout module is exactly
    the kind of file where a look-alike codepoint slipping into a comment
    would be easy to miss on review."""
    for path in (Path(uuid7_module.__file__), Path(__file__)):
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"
