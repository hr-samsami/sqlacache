# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
