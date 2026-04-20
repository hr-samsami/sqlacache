from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from sqlalchemy import ForeignKey
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from sqlacache import CacheManager, configure
from sqlacache.transport.cashews import CashewsTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    orders: Mapped[list["Order"]] = relationship(back_populates="user")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    label: Mapped[str]
    user: Mapped[User] = relationship(back_populates="orders")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    price: Mapped[int]


class CompositeRecord(Base):
    __tablename__ = "composite_records"

    tenant_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str]


@pytest.fixture
def models() -> dict[str, type[Base]]:
    return {
        "User": User,
        "Product": Product,
        "CompositeRecord": CompositeRecord,
    }


@pytest_asyncio.fixture
async def async_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session(async_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as db_session:
        yield db_session


@pytest_asyncio.fixture
async def cache(async_engine: AsyncEngine) -> AsyncIterator[CacheManager]:
    manager = configure(
        backend="mem://",
        models={
            f"{User.__module__}.{User.__name__}": {"ops": "all", "timeout": 60},
            f"{Product.__module__}.{Product.__name__}": {"ops": "all", "timeout": 60},
            f"{CompositeRecord.__module__}.{CompositeRecord.__name__}": {"ops": "all", "timeout": 60},
            "*": {"timeout": 60},
        },
    )
    await manager.bind(async_engine)
    try:
        yield manager
    finally:
        await manager.disconnect()


@pytest.fixture
def redis_test_url() -> str | None:
    return os.getenv("SQLACACHE_TEST_REDIS_URL")


@pytest_asyncio.fixture
async def memory_transport() -> AsyncIterator[CashewsTransport]:
    transport = CashewsTransport("mem://")
    await transport.connect()
    try:
        yield transport
    finally:
        await transport.disconnect()
