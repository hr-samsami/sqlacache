# Contributing

## Environment

```bash
uv sync --extra redis --group dev
prek install
```

## Common Commands

```bash
make lint
make format
make typecheck
make test
```

## Current Scope

- The implemented runtime is async SQLAlchemy
- Redis integration is supported and tested
- Sync session support is not implemented yet
- The architecture document includes planned work beyond the current MVP

## Release Smoke Test

To verify the built package outside the repo, run:

```bash
.venv/bin/python -m build
.venv/bin/python -m twine check dist/*
python -m venv /tmp/sqlacache-release-venv
UV_CACHE_DIR=.uv-cache uv pip install --python /tmp/sqlacache-release-venv/bin/python dist/*.whl
UV_CACHE_DIR=.uv-cache uv pip install --python /tmp/sqlacache-release-venv/bin/python redis aiosqlite
```

Then run a standalone script from `/tmp` that imports `sqlacache` from that external environment and validates:

- automatic `session.get(...)` caching
- Redis keys appearing after cache population
- invalidation after an update
- recache with fresh data after invalidation

## Workflow

- Keep changes focused and aligned with the active OpenSpec change.
- Run the relevant local checks before opening a pull request.
- CI should pass before merging.
