"""data patch: fix moved payouts - normalize comment and ensure Feb period

Revision ID: 20260302_0045
Revises: 20260302_0044
Create Date: 2026-03-02

"""

from __future__ import annotations

import logging

from alembic import op
import sqlalchemy as sa


revision = "20260302_0045"
down_revision = "20260302_0044"
branch_labels = None
depends_on = None


_NOTE_BASE = "выплачено 01.03.2026"
_NOTE_PLAIN = "Выплачено 01.03.2026"
_NOTE_PAREN = "(Выплачено 01.03.2026)"

_FEB_START = "2026-02-01"
_FEB_END = "2026-02-28"
_MAR_START = "2026-03-01"
_MAR_END = "2026-03-31"


def upgrade() -> None:
    logger = logging.getLogger(__name__)
    conn = op.get_bind()

    # target only payouts that were moved (or should be moved) from 01.03.2026:
    # - still on 01.03.2026
    # - already on 28.02.2026 but still has March period
    # - already contains the note (any case) or has a broken 'None ...' comment
    where_sql = """
        DATE(created_at) = DATE '2026-03-01'
        OR (
            DATE(created_at) = DATE '2026-02-28'
            AND period_start = DATE '2026-03-01'
            AND period_end = DATE '2026-03-31'
        )
        OR (
            DATE(created_at) = DATE '2026-02-28'
            AND (
                comment IS NULL
                OR BTRIM(comment) = ''
                OR LOWER(BTRIM(comment)) LIKE 'none%'
                OR POSITION(:note_base IN LOWER(comment)) > 0
            )
        )
    """

    found = conn.execute(
        sa.text(f"SELECT COUNT(*) FROM salary_payouts WHERE {where_sql}"),
        {"note_base": _NOTE_BASE},
    ).scalar_one()

    sample_ids = list(
        conn.execute(
            sa.text(
                f"""
                SELECT id
                FROM salary_payouts
                WHERE {where_sql}
                ORDER BY id
                LIMIT 3
                """
            ),
            {"note_base": _NOTE_BASE},
        ).scalars().all()
    )

    # Normalize comment:
    # 1) remove leading 'None' (case-insensitive)
    # 2) if empty -> NOTE_PLAIN
    # 3) else if already contains note (any case) -> keep
    # 4) else append NOTE_PAREN
    res = conn.execute(
        sa.text(
            f"""
            UPDATE salary_payouts
            SET
                created_at = CASE
                    WHEN DATE(created_at) = DATE '2026-03-01' THEN created_at - INTERVAL '1 day'
                    ELSE created_at
                END,
                period_start = CASE
                    WHEN salary_payouts.period_start = DATE '2026-03-01' AND salary_payouts.period_end = DATE '2026-03-31' THEN DATE '2026-02-01'
                    ELSE salary_payouts.period_start
                END,
                period_end = CASE
                    WHEN salary_payouts.period_start = DATE '2026-03-01' AND salary_payouts.period_end = DATE '2026-03-31' THEN DATE '2026-02-28'
                    ELSE salary_payouts.period_end
                END,
                comment = (
                    CASE
                        WHEN x._c_clean IS NULL OR BTRIM(x._c_clean) = '' THEN :note_plain
                        WHEN POSITION(:note_base IN LOWER(x._c_clean)) > 0 THEN x._c_clean
                        ELSE (x._c_clean || ' ' || :note_paren)
                    END
                )
            FROM (
                SELECT
                    id,
                    NULLIF(
                        BTRIM(
                            regexp_replace(COALESCE(comment, ''), '(?i)^none\\s*', '')
                        ),
                        ''
                    ) AS _c_clean
                FROM salary_payouts
            ) AS x
            WHERE salary_payouts.id = x.id
              AND ({where_sql})
            """
        ),
        {
            "note_base": _NOTE_BASE,
            "note_plain": _NOTE_PLAIN,
            "note_paren": _NOTE_PAREN,
        },
    )

    updated = int(getattr(res, "rowcount", 0) or 0)

    try:
        logger.info(
            "salary_payouts_fix_moved_normalize_comment_and_period",
            extra={"found": int(found or 0), "updated": int(updated), "sample_ids": sample_ids},
        )
        logger.debug(
            "salary_payouts_fix_moved_normalize_comment_and_period_samples",
            extra={"sample_ids": sample_ids},
        )
    except Exception:
        pass


def downgrade() -> None:
    # no-op (data patch)
    pass
