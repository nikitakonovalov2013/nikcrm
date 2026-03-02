"""data patch: move payouts from 2026-03-01 to 2026-02-28 with note

Revision ID: 20260302_0043
Revises: 20260302_0042
Create Date: 2026-03-02

"""

from __future__ import annotations

import logging

from alembic import op
import sqlalchemy as sa


revision = "20260302_0043"
down_revision = "20260302_0042"
branch_labels = None
depends_on = None


_NOTE_PLAIN = "выплачено 01.03.2026"
_NOTE_PAREN = "(выплачено 01.03.2026)"


def upgrade() -> None:
    logger = logging.getLogger(__name__)

    conn = op.get_bind()

    found = conn.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM salary_payouts
            WHERE DATE(created_at) = DATE '2026-03-01'
            """
        )
    ).scalar_one()

    sample_ids = list(
        conn.execute(
            sa.text(
                """
                SELECT id
                FROM salary_payouts
                WHERE DATE(created_at) = DATE '2026-03-01'
                ORDER BY id
                LIMIT 3
                """
            )
        ).scalars().all()
    )

    res = conn.execute(
        sa.text(
            """
            UPDATE salary_payouts
            SET
                created_at = created_at - INTERVAL '1 day',
                comment = CASE
                    WHEN comment IS NULL OR BTRIM(comment) = '' THEN :note_plain
                    WHEN POSITION(:note_plain IN comment) > 0 THEN comment
                    ELSE (comment || ' ' || :note_paren)
                END
            WHERE DATE(created_at) = DATE '2026-03-01'
            """
        ),
        {"note_plain": _NOTE_PLAIN, "note_paren": _NOTE_PAREN},
    )

    updated = int(getattr(res, "rowcount", 0) or 0)

    try:
        logger.info(
            "salary_payouts_patch_march1_to_feb28",
            extra={"found": int(found or 0), "updated": int(updated), "sample_ids": sample_ids},
        )
        logger.debug(
            "salary_payouts_patch_march1_to_feb28_samples",
            extra={"sample_ids": sample_ids},
        )
    except Exception:
        pass


def downgrade() -> None:
    # no-op (data patch)
    pass
