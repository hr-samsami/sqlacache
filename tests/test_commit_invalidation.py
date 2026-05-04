"""End-to-end tests for commit-time invalidation.

These exercise the full flow (AsyncSession → flush → commit/rollback → cache)
rather than mocking out the manager's internal scheduling methods. They catch
bugs that unit tests with mocked ``_record_*`` methods can't, like:

- session object identity across flush and commit hooks
- rolled-back transactions leaving stale invalidations behind
- subsequent reads after a commit seeing the invalidation

Post-commit invalidation is scheduled on the running event loop, so tests that
read immediately after a commit call ``cache.flush_pending()`` first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select, update

from .conftest import User

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from sqlacache.manager import CacheManager


class TestCommitTimeInvalidation:
    async def test_commit_invalidates_row_cache(self, cache: CacheManager, session: AsyncSession) -> None:
        """After commit, the row read should reflect the new value, not the cached one."""

        session.add(User(id=1, name="original"))
        await session.commit()
        await cache.flush_pending()

        # Prime the cache.
        user = await session.get(User, 1)
        assert user is not None
        assert user.name == "original"

        # Mutate and commit.
        user.name = "updated"
        await session.commit()
        await cache.flush_pending()

        # Re-read: should see the updated value, not cached "original".
        session.expunge_all()
        refreshed = await session.get(User, 1)
        assert refreshed is not None
        assert refreshed.name == "updated"

    async def test_rollback_preserves_cache(self, cache: CacheManager, session: AsyncSession) -> None:
        """Rollback must not evict cache entries built from committed state."""

        session.add(User(id=2, name="committed"))
        await session.commit()
        await cache.flush_pending()

        # Prime the cache with the committed value.
        user = await session.get(User, 2)
        assert user is not None
        assert user.name == "committed"

        # Start a mutation and roll it back. The mapper events should record
        # the pending invalidation, and after_rollback should drop it.
        user.name = "never-committed"
        await session.flush()
        await session.rollback()

        # No pending invalidations should be left on the session.
        assert session.sync_session not in cache._pending

    async def test_bulk_update_invalidates_on_commit(self, cache: CacheManager, session: AsyncSession) -> None:
        """Bulk UPDATE should bump table version after commit."""

        session.add_all([User(id=10, name="a"), User(id=11, name="b")])
        await session.commit()
        await cache.flush_pending()

        # Prime.
        result = await session.execute(select(User).where(User.id.in_([10, 11])).order_by(User.id))
        users = result.scalars().all()
        assert [u.name for u in users] == ["a", "b"]

        # Bulk update + commit.
        await session.execute(update(User).where(User.id.in_([10, 11])).values(name="renamed"))
        await session.commit()
        await cache.flush_pending()

        # Re-fetch: should see "renamed".
        session.expunge_all()
        result = await session.execute(select(User).where(User.id.in_([10, 11])).order_by(User.id))
        users = result.scalars().all()
        assert all(u.name == "renamed" for u in users)

    async def test_bulk_update_rollback_does_not_invalidate(self, cache: CacheManager, session: AsyncSession) -> None:
        session.add(User(id=20, name="original"))
        await session.commit()
        await cache.flush_pending()

        # Prime.
        user = await session.get(User, 20)
        assert user is not None and user.name == "original"

        # Bulk update, then rollback.
        await session.execute(update(User).where(User.id == 20).values(name="will-rollback"))
        await session.rollback()

        # No pending invalidations should remain.
        assert session.sync_session not in cache._pending

    async def test_delete_invalidates_on_commit(self, cache: CacheManager, session: AsyncSession) -> None:
        session.add(User(id=30, name="doomed"))
        await session.commit()
        await cache.flush_pending()

        user = await session.get(User, 30)
        assert user is not None

        await session.delete(user)
        await session.commit()
        await cache.flush_pending()

        session.expunge_all()
        result = await session.get(User, 30)
        assert result is None

    async def test_multiple_mutations_single_commit(self, cache: CacheManager, session: AsyncSession) -> None:
        """Several row changes in one transaction should all be applied atomically."""

        session.add_all([User(id=40, name="p"), User(id=41, name="q"), User(id=42, name="r")])
        await session.commit()
        await cache.flush_pending()

        # Prime all three.
        for pk in (40, 41, 42):
            u = await session.get(User, pk)
            assert u is not None

        # Update all three in one transaction.
        for pk, new_name in ((40, "P"), (41, "Q"), (42, "R")):
            u = await session.get(User, pk)
            assert u is not None
            u.name = new_name
        await session.commit()
        await cache.flush_pending()

        # All three should reflect the new values.
        session.expunge_all()
        for pk, expected in ((40, "P"), (41, "Q"), (42, "R")):
            u = await session.get(User, pk)
            assert u is not None
            assert u.name == expected

    async def test_bulk_delete_invalidates_on_commit(self, cache: CacheManager, session: AsyncSession) -> None:
        session.add_all([User(id=50, name="x"), User(id=51, name="y")])
        await session.commit()
        await cache.flush_pending()

        # Prime.
        result = await session.execute(select(User).where(User.id.in_([50, 51])))
        assert len(result.scalars().all()) == 2

        # Bulk delete + commit.
        await session.execute(delete(User).where(User.id.in_([50, 51])))
        await session.commit()
        await cache.flush_pending()

        session.expunge_all()
        result = await session.execute(select(User).where(User.id.in_([50, 51])))
        assert result.scalars().all() == []

    async def test_no_invalidation_without_commit(self, cache: CacheManager, session: AsyncSession) -> None:
        """Mapper events alone (flush without commit) should not invalidate."""

        session.add(User(id=60, name="draft"))
        await session.flush()

        # The pending set should have the insert recorded.
        pending = cache._pending.get(session.sync_session)
        assert pending is not None
        assert User in pending["rows"]
        assert 60 in pending["rows"][User]

        await session.rollback()

        # Rollback clears it.
        assert session.sync_session not in cache._pending


class TestEagerLoadBypass:
    """Statements with eager relationship loaders bypass the cache.

    Caching them would silently return stale joined data when a related row
    changes, since sqlacache doesn't track the relationship as a dependency.
    """

    async def test_selectinload_bypasses_cache(self, cache: CacheManager, session: AsyncSession, caplog: Any) -> None:
        import logging

        from sqlalchemy.orm import selectinload

        from .conftest import Order

        session.add(User(id=70, name="u"))
        await session.commit()
        await cache.flush_pending()
        session.add(Order(id=1, user_id=70, label="first"))
        await session.commit()
        await cache.flush_pending()

        with caplog.at_level(logging.WARNING, logger="sqlacache.interceptor"):
            result = await session.execute(select(User).where(User.id == 70).options(selectinload(User.orders)))
            user = result.scalar_one()
            assert user.name == "u"

        assert any("bypassing cache" in rec.message for rec in caplog.records)
