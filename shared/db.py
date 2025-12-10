from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import MetaData
from contextlib import asynccontextmanager
from .config import settings
import logging


convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=convention)


class Base(DeclarativeBase):
    metadata = metadata


logging.getLogger(__name__).info(
    "creating async engine", extra={"url": settings.DATABASE_URL.replace(settings.POSTGRES_PASSWORD, "***") if settings.POSTGRES_PASSWORD else settings.DATABASE_URL}
)
engine = create_async_engine(settings.DATABASE_URL, echo=False, pool_pre_ping=True)

AsyncSessionLocal = async_sessionmaker[
    AsyncSession
](bind=engine, expire_on_commit=False, autoflush=False, autocommit=False)


@asynccontextmanager
async def get_async_session() -> AsyncSession:
    session = AsyncSessionLocal()
    try:
        logging.getLogger(__name__).debug("db session begin")
        yield session
        await session.commit()
        logging.getLogger(__name__).debug("db session commit")
    except Exception:
        logging.getLogger(__name__).exception("db session rollback due to error")
        await session.rollback()
        raise
    finally:
        await session.close()
        logging.getLogger(__name__).debug("db session closed")
