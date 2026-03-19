from __future__ import annotations

import asyncio
import logging

from shared.db import AsyncSessionLocal
from shared.services.finance_sync import backfill_salary_payout_operations


logger = logging.getLogger(__name__)


async def _run() -> int:
    async with AsyncSessionLocal() as session:
        try:
            res = await backfill_salary_payout_operations(session=session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("finance salary payouts backfill failed")
            return 1

    processed = int((res or {}).get("processed") or 0)
    print(f"finance salary payout backfill complete: processed={processed}")
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
