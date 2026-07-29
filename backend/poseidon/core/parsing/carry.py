"""Cross-turn slot carry (doc 02 section 5, ported verbatim from TM1's
``ConversationBuffer``): a slot the turn did not mention carries the
previous value forward, an explicit clear sets it to ``None``, and a newly
resolved value replaces the old one outright -- slots never merge.

:class:`SlotUpdates` is how a parsing stage reports what one turn found:
each field is tri-state -- the module-level :data:`UNSET` sentinel (the
default) means "not mentioned, carry the prior value", ``None`` means
"explicitly cleared", and anything else is the replacement value.
:func:`apply_carry` folds a :class:`SlotUpdates` onto the prior
:class:`~poseidon.core.skills.context.ConversationSlots`, producing the
next turn's slots.
"""

from dataclasses import dataclass, replace
from datetime import date

from poseidon.core.skills.context import ConversationSlots


class _UnsetType:
    """Sentinel type for "this turn said nothing about this slot."

    A dedicated class -- rather than, say, a bare ``object()`` -- so the
    tri-state is nameable in a type annotation (``str | None | _UnsetType``)
    and self-describing in a debugger/repr, while staying distinct from
    every real slot value: no slot's actual value is ever a
    :class:`_UnsetType`, only ``None`` (explicit clear) or the resolved
    value. Leading underscore: callers use the :data:`UNSET` singleton
    below, not this class directly.
    """

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _UnsetType()
"""The one sentinel callers use. ``SlotUpdates`` fields default to it."""


@dataclass(frozen=True)
class SlotUpdates:
    """What one turn resolved for the four slots that carry across turns.

    Each field is tri-state: :data:`UNSET` (default) -- the turn did not
    mention this slot, carry the prior value; ``None`` -- the turn
    explicitly cleared it; any other value -- the turn resolved a new
    value, which REPLACES the prior one outright (doc 02 section 5: never
    merge). ``mode``/``region``/``topic``/``pass_through`` are not here:
    nothing in Phase 4 Task 1 resolves them, so :func:`apply_carry` always
    carries them forward unchanged (later tasks/phases give them their own
    update paths without reshaping this dataclass).
    """

    customer: str | None | _UnsetType = UNSET
    port: str | None | _UnsetType = UNSET
    period_a: date | None | _UnsetType = UNSET
    period_b: date | None | _UnsetType = UNSET


def _carried(prior: object, update: object) -> object:
    """One field's carry rule: UNSET (any instance) keeps ``prior``;
    anything else -- including ``None`` -- replaces it outright."""
    return prior if isinstance(update, _UnsetType) else update


def apply_carry(slots: ConversationSlots, updates: SlotUpdates) -> ConversationSlots:
    """Fold ``updates`` onto ``slots`` under the tri-state carry rule.

    Only the four slots :class:`SlotUpdates` knows about can change here;
    every other :class:`ConversationSlots` field (``mode``, ``region``,
    ``topic``, ``pass_through``) is carried forward untouched via
    :func:`dataclasses.replace` -- so a later, additive growth of
    ``ConversationSlots`` needs no change here to keep carrying correctly.
    The :data:`UNSET` sentinel never appears in the result: every field is
    resolved to a real value (``prior`` or ``update``) before the new
    ``ConversationSlots`` is built.
    """
    return replace(
        slots,
        customer=_carried(slots.customer, updates.customer),
        port=_carried(slots.port, updates.port),
        period_a=_carried(slots.period_a, updates.period_a),
        period_b=_carried(slots.period_b, updates.period_b),
    )
