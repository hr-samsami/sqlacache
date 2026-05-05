# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.3] - 2026-05-05

### Fixed

- Reads immediately after `session.commit()` could still return stale cached values.
- Rolled-back transactions were incorrectly evicting valid cache entries.
- Unflushed in-session writes could be overridden by cached rows.
- Cross-process invalidation could be applied twice for the same event.
- Health checks could fail spuriously on certain stored values.
- Composite primary keys produced non-deterministic cache tags across runtimes.
- Query literals containing `"count("` or `"exists("` were misclassified as those operation types.
- Cache keys used a SQLite dialect, which could mangle Postgres-specific syntax.
- Redis pub/sub listener stopped reconnecting after a connection error.
- Package metadata URLs pointed to a non-existent repository.

### Added

- `cache_manager.flush_pending()` — await in-flight post-commit invalidations.
- `RedisPubSub.is_healthy()` — check listener connection state (useful for health endpoints).
- `RedisPubSub.add_callback()` — register invalidation event handlers (`listen()` is now deprecated).
- Warnings logged when a configured model path can't be imported or a `None` PK row is skipped.

### Changed

- Queries with eager-loaded relationships (`selectinload`, `joinedload`, etc.) bypass the cache and always hit the database.
- Architecture and roadmap doc moved to `docs/architecture-and-roadmap.md`.

### Removed

- `configure(invalidation=...)` parameter — had no effect.
- Sync session methods (`bind_sync`, `execute_sync`, `invalidate_sync`) — sync support is deferred to v0.2.
- `sqlacache.serializers` module — was never used.

## [0.1.1] - 2026-04-09

### Changed

- Updated package description for clarity.

## [0.1.0] - 2026-04-09

### Added

- First alpha release.
- Automatic caching for async SQLAlchemy reads — no decorators or query changes required.
- Row-level cache invalidation when records are created, updated, or deleted.
- Declarative config to map models to cache rules and TTLs in one place.
- Redis and in-memory backends supported out of the box.
- All workers stay in sync — cache invalidation propagates across processes via Redis.
- Manual override APIs for edge cases where automatic behavior isn't enough.
