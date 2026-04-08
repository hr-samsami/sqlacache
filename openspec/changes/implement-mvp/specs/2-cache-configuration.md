# Spec: Cache Configuration

Implement the `configure()` factory function, `CacheManager` class, and model-to-ops configuration parsing.

## Requirements

### R1: Configuration Schema

Define configuration structure supporting both dict and URL forms:

```python
# Form A: Backend as URL string (simplest)
config = {
    "backend": "redis://localhost:6379/1",
    "models": {
        "myapp.models.User": {"ops": {"get", "fetch"}, "timeout": 900},
        "myapp.models.Product": {"ops": "all", "timeout": 3600},
        "*": {"timeout": 3600},
    },
}

# Form B: Backend as dict (full control)
config = {
    "backend": {
        "url": "redis://localhost:6379/1",
        "pickle_type": "sqlalchemy",
        "compress_type": "gzip",
    },
    "models": {...},
}

# Form C: In-memory backend (dev/test)
config = {
    "backend": "mem://",
    "models": {...},
}
```

Model-to-ops mapping rules:

- `"ops"` can be a string (`"all"` → `{"get", "fetch", "count", "exists"}`), a set, or a frozenset
- `"timeout"` is required per model (or from `"*"` wildcard)
- `None` as a model value means "explicitly disable caching for this model"
- Wildcard `"*"` acts as a fallback default

### R2: `configure()` Factory Function

Implement `sqlacache.configure(**kwargs)` that:

1. Accepts:
   - `backend: str | dict` — cashews URL or dict config
   - `models: dict[str, dict | None]` — model-to-ops mapping
   - `serializer: str` (optional, default `"sqlalchemy"`) — passed to cashews as `pickle_type`
   - `invalidation: str` (optional, default `"row"`) — `"row"` or `"table"` (for future use)
   - `prefix: str` (optional, default `"sqlacache"`) — cache key prefix
   - `default_timeout: int` (optional, default `3600`) — fallback TTL
   - `compress: str` (optional) — `None`, `"gzip"`, or `"zlib"`, passed to cashews

2. Returns a `CacheManager` instance

3. Normalizes:
   - Ops: convert `"all"` to `{"get", "fetch", "count", "exists"}`
   - Resolves wildcard `"*"` defaults
   - Validates model strings are valid import paths (e.g., `"app.models.User"`)

4. Raises:
   - `ConfigError` if backend URL is invalid
   - `ConfigError` if model config is malformed
   - `ConfigError` if required timeout is missing for a model

### R3: `CacheManager` Class

Implement `CacheManager` supporting both async and sync APIs:

```python
class CacheManager:
    def __init__(self, config: dict):
        """Initialize from configuration dict."""
        self._config = config
        self._transport = None  # Set on bind()
        self._model_config = {}  # Normalized model-to-ops mapping
        self._pubsub_task = None

    async def bind(self, engine):
        """Register ORM event hooks and initialize transport."""
        # Initialize transport (CashewsTransport with backend URL)
        # Register do_orm_execute hook for query interception
        # Register after_insert/after_update/after_delete hooks for invalidation
        # Start cross-process invalidation listener (background task)
        pass

    async def execute(self, session, statement, timeout: int = None):
        """Manually cache a query (for ops=() models or custom queries)."""
        pass

    async def invalidate(self, model: type = None, pks: list = None):
        """Manually invalidate cache entries."""
        # If model + pks: delete tags for those specific rows
        # If model only: table-level invalidation
        # If neither: invalidate all
        pass

    async def invalidate_all(self):
        """Invalidate entire cache."""
        pass

    def get_model_config(self, model: type) -> dict | None:
        """Get config for a model (with wildcard fallback)."""
        # Look up model in config
        # Return normalized config or None if explicitly disabled
        pass

    async def disconnect(self):
        """Close transport connection and stop pub/sub listener."""
        pass

    # Sync wrappers (for sync sessions)
    def bind_sync(self, engine):
        """Sync wrapper for bind()."""
        pass

    def execute_sync(self, session, statement, timeout: int = None):
        """Sync wrapper for execute()."""
        pass

    def invalidate_sync(self, model: type = None, pks: list = None):
        """Sync wrapper for invalidate()."""
        pass
```

Optional: Provide convenience properties:

- `.is_enabled(model: type, op: str) -> bool` — Check if caching is enabled for a model+op combo
- `.model_by_tablename(tablename: str) -> type | None` — Reverse lookup (useful in event hooks)

### R4: Model Import Resolution

Implement helper `_resolve_model(model_path: str) -> type` that:

- Takes a string like `"myapp.models.User"`
- Dynamically imports and returns the class
- Raises `ConfigError` with helpful message if import fails

This is used during `configure()` to validate model paths.

### R5: Ops Normalization

Create a helper function to normalize operations:

```python
def normalize_ops(ops_config):
    """Convert 'all' or string to set of ops."""
    if ops_config is None:
        return set()
    if ops_config == "all":
        return {"get", "fetch", "count", "exists"}
    if isinstance(ops_config, str):
        raise ConfigError(f"Unknown ops: {ops_config!r}")
    return set(ops_config)  # frozenset or list → set
```

Valid ops: `"get"`, `"fetch"`, `"count"`, `"exists"`.

### R6: Configuration Validation

On `configure()` call, validate:

- Backend URL or dict is valid (attempt to parse)
- Models dict is not empty
- For each model:
  - ops is valid (set, frozenset, "all", or None)
  - timeout is an int > 0 (or present in `"*"` wildcard)
  - model path can be imported (if not wildcard)

### R7: Environment Variable Support (Optional, v0.1.0)

Support loading backend from env var:

```python
cache = configure(
    backend=os.getenv("SQLACACHE_BACKEND", "mem://"),
    models={...},
)
```

No special parsing — just pass through to CashewsTransport. Documented as "optional feature" in README.

### R8: Public API Exports

In `src/sqlacache/__init__.py`, export:

- `configure`
- `CacheManager`
- `ConfigError` exception
- `__version__`

## Implementation Notes

- Config parsing is synchronous; async operations happen only on `bind()`
- Model import resolution should be lazy (on `bind()` or first access), not during `configure()`, to allow registering models after cache initialization
- Wildcard matching: if model is not in config, check `"*"`; if not found, caching is disabled for that model
- Store normalized config (ops as frozensets, timeouts resolved) internally to avoid re-processing on every query

## Acceptance Criteria

- ✓ `configure()` accepts all parameter forms (URL, dict, in-memory)
- ✓ `CacheManager` can be instantiated from config dict
- ✓ Model config lookup works with wildcard fallback
- ✓ Ops normalization works (set, "all", None → correct internal representation)
- ✓ `ConfigError` raised for invalid backend URL, missing timeout, unknown ops
- ✓ Public exports in `__init__.py` work
- ✓ Sync wrappers exist (even if not fully tested yet)
