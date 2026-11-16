"""``persist_brief`` -- render a brief and store it in ``brief_run`` (D83).

Shared by ``existing_customer_brief`` and ``new_prospect_brief``. The brief's
HTML, PDF and markdown go into one ``brief_run`` row; the PDF is served by
``/api/briefs/{id}/pdf`` like a report, so the artifact link is an app-relative
path rather than a presigned URL.

A failed store write never fails the brief: it degrades to a proof line (the
parts the user reads are already emitted).
"""

import logging
from datetime import UTC, date, datetime
from typing import cast

from poseidon.briefs.store import NewBrief
from poseidon.core.documents import ANONYMOUS_SUB, safe_filename
from poseidon.core.identity import UserContext
from poseidon.core.skills.context import ArtifactRef, SkillContext
from poseidon.core.util.uuid7 import uuid7

from .build_brief_pdf import render_brief

logger = logging.getLogger(__name__)

BRIEF_SKIP_PROOF = "Brief: skipped (no brief store configured)"


def _now() -> datetime:
    return datetime.now(UTC)


def persist_brief(
    ctx: SkillContext,
    *,
    kind: str,
    subject: str,
    title: str,
    anchor_date: date,
    markdown_body: str,
) -> tuple[list[ArtifactRef], list[str]]:
    """Render and store a brief; returns ``(artifacts, proof lines)``."""
    if ctx.brief_store is None:
        return [], [BRIEF_SKIP_PROOF]

    rendered = render_brief(title, markdown_body)
    brief_id = str(uuid7())
    user = cast(UserContext | None, ctx.user)
    created_by = user.sub if user is not None else ANONYMOUS_SUB
    try:
        ctx.brief_store.insert_brief(
            created_by,
            NewBrief(
                id=brief_id,
                kind=kind,
                subject=subject,
                title=title,
                anchor_date=anchor_date,
                status="ok",
                markdown=markdown_body,
                html=rendered.html.encode("utf-8"),
                pdf=rendered.pdf,
                pdf_error=rendered.pdf_error,
                created_by=created_by,
                conversation_id=ctx.conversation_id,
                generated_at=_now(),
            ),
        )
    except Exception as exc:  # noqa: BLE001 - a store failure must not fail the brief
        logger.error("brief %s not stored: %s: %s", brief_id, type(exc).__name__, exc)
        return [], [f"Brief: not stored: {type(exc).__name__}"]

    proof = [f"Brief: {brief_id}"]
    artifacts: list[ArtifactRef] = []
    if rendered.pdf is not None:
        proof.append(f"Pages: {rendered.page_count}")
        proof.append(f"Bytes: {len(rendered.pdf)}")
        artifacts.append(
            ArtifactRef(
                name=safe_filename(title) + ".pdf",
                url=f"/api/briefs/{brief_id}/pdf",
                mime="application/pdf",
            )
        )
    else:
        proof.append(f"PDF not rendered: {rendered.pdf_error}")
    return artifacts, proof
