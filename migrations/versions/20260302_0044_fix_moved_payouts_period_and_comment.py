"""data patch: fix moved payouts - update period to Feb + normalize comment

Revision ID: 20260302_0044
Revises: 20260302_0043
Create Date: 2026-03-02

"""

from __future__ import annotations

import logging

from alembic import op
import sqlalchemy as sa


revision = "20260302_0044"
down_revision = "20260302_0043"
branch_labels = None
depends_on = None


_NOTE_PLAIN = "Выплачено 01.03.2026"
_NOTE_PAREN = "(Выплачено 01.03.2026)"

_FEB_START = "2026-02-01"
_FEB_END = "2026-02-28"
_MAR_START = "2026-03-01"
_MAR_END = "2026-03-31"


def upgrade() -> None:
    logger = logging.getLogger(__name__)
    conn = op.get_bind()

    found = conn.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM salary_payouts
            WHERE
                DATE(created_at) = DATE '2026-03-01'
                OR (
                    DATE(created_at) = DATE '2026-02-28'
                    AND period_start = DATE '2026-03-01'
                    AND period_end = DATE '2026-03-31'
                )
            """
        )
    ).scalar_one()

    sample_ids = list(
        conn.execute(
            sa.text(
                """
                SELECT id
                FROM salary_payouts
                WHERE
                    DATE(created_at) = DATE '2026-03-01'
                    OR (
                        DATE(created_at) = DATE '2026-02-28'
                        AND period_start = DATE '2026-03-01'
                        AND period_end = DATE '2026-03-31'
                    )
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
                created_at = CASE
                    WHEN DATE(created_at) = DATE '2026-03-01' THEN created_at - INTERVAL '1 day'
                    ELSE created_at
                END,
                period_start = DATE '2026-02-01',
                period_end = DATE '2026-02-28',
                comment = CASE
                    WHEN comment IS NULL OR BTRIM(comment) = '' THEN :note_plain
                    WHEN LOWER(BTRIM(comment)) LIKE 'none%' THEN :note_plain
                    WHEN LOWER(comment) LIKE '%' || LOWER(:note_plain) || '%' THEN comment
                    ELSE (comment || ' ' || :note_paren)
                END
            WHERE
                DATE(created_at) = DATE '2026-03-01'
                OR (
                    DATE(created_at) = DATE '2026-02-28'
                    AND period_start = DATE '2026-03-01'
                    AND period_end = DATE '2026-03-31'
                )
            """
        ),
        {"note_plain": _NOTE_PLAIN, "note_paren": _NOTE_PAREN},
    )

    updated = int(getattr(res, "rowcount", 0) or 0)

    try:
        logger.info(
            "salary_payouts_fix_moved_period_and_comment",
            extra={"found": int(found or 0), "updated": int(updated), "sample_ids": sample_ids},
        )
        logger.debug(
            "salary_payouts_fix_moved_period_and_comment_samples",
            extra={"sample_ids": sample_ids},
        )
    except Exception:
        pass


def downgrade() -> None:
    # no-op (data patch)
    pass
