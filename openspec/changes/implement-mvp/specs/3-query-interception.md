# Spec: Query Interception

Implement SQLAlchemy `do_orm_execute` event hook for automatic query caching and cache-aware query execution.

## Requirements

### R1: Event Hook Registration

On `CacheManager.bind(engine)`:

1. Register a handler for the `do_orm_execute` event on the engine
2. Handler must work for both sync and async sessions
3. Async sessions (AsyncSession) come through the same `do_orm_execute` event (SQLAlchemy 1.4+)

Implementation:

```python
from sqlalchemy import event
from sqlalchemy.orm import Session

@event.listens_for(Session, "do_orm_execute", propagate=True)
def intercept_query(execute_state):
    """Hook into all ORM query execution."""
    # Extract model(s) and operation type
    # Check cache config and cache status
    # Either return cached result or execute normally
```

### R2: Operation Type Detection

Extract the operation type from the SQL statement:

- **`"get"`**: Single-row lookups
  - `session.get(Model, pk)`
  - Identified by: `SELECT ... WHERE Model.id = ?` and `statement.is_select == True` and `limit=1` (or inferred)
  - Alternative: `select(Model).where(Model.id == ...).first()`
  - **Check**: SQLAlchemy AST for `session.get()` calls or detect `.first()` on a WHERE-limited select

- **`"fetch"`**: Multi-row queries
  - `select(Model).where(...).all()`
  - Any `SELECT` returning multiple rows
  - Uses `.all()` or `.scalars().all()` or implicit without `.first()`
  - **Check**: SQLAlchemy AST for absence of `.first()` or `.one()`

- **`"count"`**: Aggregation queries
  - `select(func.count(Model.id))`
  - Identified by: SQL contains `COUNT(*)`
  - **Check**: AST for `func.count(...)` or SQL string has `COUNT`

- **`"exists"`**: Existence checks
  - `select(Model).where(...).exists()`
  - Identified by: SQL has `EXISTS (...)`
  - **Check**: AST for `.exists()` method

### R3: Model Extraction from Statement

Extract the target model class from the SQLAlchemy statement:

```python
def extract_model_from_statement(stmt) -> type | list[type]:
    """
    Extract model(s) from a select statement.

    Returns:
    - Single model for: select(User).where(...)
    - List of models for: select(User, Order).join(...)
    - None for: raw SQL, func aggregates, etc.
    """
```

Implementation approach:

- Use `stmt._from_obj` or `stmt.froms` to get the FROM clause table(s)
- Map table to model via SQLAlchemy's mapper registry
- For complex joins, extract all tables in the FROM clause

### R4: Statement Cacheability Check

Before attempting to cache, verify:

1. Statement is a SELECT (not INSERT/UPDATE/DELETE)
2. Statement does not contain raw SQL (only ORM constructs)
3. Statement contains at least one model (not just `SELECT 1` or aggregates)
4. Model(s) are in the cache configuration

### R5: Cache Lookup Flow

When an ORM query executes:

```
do_orm_execute(execute_state)
  ├─> Extract model(s) from statement
  ├─> Determine op type (get, fetch, count, exists)
  ├─> Check config: is_enabled(model, op)?
  │   └─> If NO: execute normally and return (skip cache)
  ├─> Generate cache key from statement + parameters (see cache-key-generation spec)
  ├─> Check cache.get(key)?
  │   └─> If HIT: deserialize and return (skip DB)
  │   └─> If MISS: fall through
  ├─> Execute statement normally (invoke_statement)
  ├─> Extract PKs from result (see row-level-invalidation spec)
  ├─> Record dependency tags + cache result
  └─> Return result
```

### R6: Async/Sync Handling

For async sessions:

- `do_orm_execute` runs synchronously but we need to call `await cache.get()` and `await cache.set()`
- **Solution**: Use `asyncio.get_running_loop()` to detect if we're in async context
- **Alternative**: Register separate hooks for async and sync via different event targets
- **Architecture decision**: SQLAlchemy's `do_orm_execute` callback is always sync, but it receives an `execute_state` object that wraps the async execution context. We need to offload async caching to a background task or use an async-aware wrapper.

**Async strategy (recommended for v0.1.0)**:

- In `do_orm_execute`, do NOT call `await cache.get()` directly (would block the sync hook)
- Instead, use a **two-phase approach**:
  1. `do_orm_execute` returns early if executing normally (cache miss)
  2. After query executes, invalidation hooks (`after_insert`, etc.) run async naturally
  3. For cache HIT, we need a way to short-circuit the statement execution — this is tricky

**Problem**: The `do_orm_execute` hook is synchronous, but our cache operations are async. We cannot `await` in a sync callback.

**Workaround options**:

A. **Use `asyncio.run()` in sync hook** (not ideal, creates nested event loops)
B. **Delay cache check to after-execute** (defeats purpose of caching)
C. **Use sync cache operations only** (incompatible with async cashews)
D. **Pre-compute cache keys in sync hook, async fetch in wrapper** (complex)
E. **For v0.1.0, only support async sessions** (simplest, documented limitation)

**Chosen approach for v0.1.0**: Support async sessions only. Sync wrapper (`CacheManager.bind_sync()`) deferred to v0.2.0. This aligns with the MVP scope (async-first).

### R7: Execution State Modification

To return a cached result without executing the statement:

```python
# The execute_state object has methods to control what happens next
execute_state.invoke_statement()  # Run the query normally
execute_state.cache_ok = True     # Mark state as cacheable

# To skip execution with a premade result:
# This is NOT directly supported by SQLAlchemy's do_orm_execute API.
# Workaround: Create a mock result cursor that returns our cached data.
```

**Known limitation**: SQLAlchemy's `do_orm_execute` doesn't have a built-in way to return a pre-calculated result without executing. We would need to:

1. Construct a `CursorResult` object manually and return it, OR
2. Fall back to NOT caching on miss (only caching on write-back), OR
3. Use a different hook point (e.g., before-execute via `before_cursor_execute`, but this loses ORM context)

**For v0.1.0**: Cache hits are handled implicitly by not modifying execute_state; cache misses are written back after normal execution. True pre-execution cache bypass is deferred.

### R8: Error Handling

If cache lookup or write fails:

- **Cache miss**: Continue with normal execution (fallback to DB)
- **Cache error (connection lost, corrupted data)**: Log warning, fallback to DB
- Never raise cache exceptions upward to application code
- Configuration option: `suppress_cache_errors` (default True) to control this behavior

## Implementation Notes

- Operation type detection can be done via SQL string matching or AST inspection; AST is more reliable but requires SQLAlchemy internals familiarity
- For async support, consider wrapping query execution in a coroutine context that can call async cache methods
- Test both `session.execute(select(...))` and `select(...).all()` patterns
- Model extraction must handle complex queries with joins and derived tables gracefully

## Acceptance Criteria

- ✓ `do_orm_execute` hook registers on `bind(engine)`
- ✓ Operation type correctly detected for get, fetch, count, exists queries
- ✓ Model extraction works for simple and multi-table queries
- ✓ Cache lookup flow executes without error (even if cache misses all tests)
- ✓ Async sessions can execute queries without cache errors
- ✓ Cache errors don't propagate to application code
- ✓ Sync sessions raise `NotImplementedError` with clear v0.2.0 messaging (or skip silently)
