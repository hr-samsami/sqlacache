# Changelog

## 0.1.0

- Initial alpha MVP release.
- Added package scaffolding, linting, typing, tests, CI, and build metadata.
- Added validated configuration and cache manager APIs via `configure(...)`.
- Added `cashews` transport integration for `mem://` and `redis://` backends.
- Added automatic async ORM caching for configured reads, including `AsyncSession.get(...)`.
- Added row-level invalidation for ORM inserts, updates, deletes, and bulk mutations.
- Added Redis pub/sub support for cross-process invalidation.
- Added unit tests, Redis integration coverage, and an external wheel-install smoke test workflow.
- Sync session support remains deferred.
