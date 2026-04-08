# Spec: Row-Level Invalidation Engine

Implement automatic row-level cache invalidation triggered by ORM write events (INSERT, UPDATE, DELETE) using cashews' native tag system.

## Requirements

### R1: Tag Generation Strategy

Generate tags from row primary keys:

```python
def generate_tags(model: type, pks: list) -> list[str]:
    """
    Generate cache dependency tags from model and primary keys.

    Args:
        model: SQLAlchemy model class
        pks: List of primary key values for affected rows

    Returns:
        List of tag strings like ["users:42", "users:55"]

    Example:
        generate_tags(User, [42, 55]) → ["users:42", "users:55"]
    """
    table_name = model.__tablename__
    tags = [f"{table_name}:{pk}" for pk in pks]
    return tags
```

Composite primary keys (e.g., `(tenant_id, user_id)`):

```python
# For composite PKs, stringify the tuple
def generate_tags(model: type, pks: list) -> list[str]:
    # pks = [(42, 1), (42, 2)]  # (tenant_id, user_id)
    table_name = model.__tablename__
    tags = [f"{table_name}:{str(pk)}" for pk in pks]
    # tags = ["users:('42', '1')", "users:('42', '2')"]
```

### R2: Primary Key Extraction

Extract PK(s) from:

1. **After INSERT** — PKs are in `target.__dict__` (ORM instance just inserted)
2. **After UPDATE** — PKs are in `target.__dict__` (PK values don't change on update, only non-PK columns do)
3. **After DELETE** — PKs are in `target.__dict__` (instance still exists in session before expunge)

Implementation:

```python
from sqlalchemy import inspect

def extract_pk_from_instance(instance) -> tuple | int:
    """
    Extract primary key value(s) from a mapped instance.

    Returns:
        Single value for single-column PK: 42
        Tuple for composite PK: (42, "tenant")
    """
    mapper = inspect(instance).mapper
    pk_cols = mapper.primary_key

    if len(pk_cols) == 1:
        # Single-column PK
        pk_col = pk_cols[0]
        return getattr(instance, pk_col.name)
    else:
        # Composite PK
        return tuple(getattr(instance, col.name) for col in pk_cols)
```

### R3: ORM Write Event Hooks

Register hooks on `CacheManager.bind()`:

```python
from sqlalchemy import event

@event.listens_for(Mapper, "after_insert", propagate=True)
def invalidate_after_insert(mapper, connection, target):
    """Invalidate cache after INSERT."""
    model = mapper.class_
    pk = extract_pk_from_instance(target)
    tags = generate_tags(model, [pk])
    # Async invalidation call (must be async-safe)
    asyncio.create_task(cache_manager._invalidate_tags(*tags))

@event.listens_for(Mapper, "after_update", propagate=True)
def invalidate_after_update(mapper, connection, target):
    """Invalidate cache after UPDATE."""
    # Same as insert

@event.listens_for(Mapper, "after_delete", propagate=True)
def invalidate_after_delete(mapper, connection, target):
    """Invalidate cache after DELETE."""
    # Same as insert
```

### R4: Cashews Tag-Based Invalidation

Use cashews' native tag system to delete all cache entries dependent on a row:

```python
async def invalidate_tags(self, *tags: str) -> None:
    """
    Delete all cache entries tagged with these tags.

    Internally, cashews:
    1. Finds all cache keys in the tag set (Redis SET)
    2. Deletes all those keys
    3. Removes the tag set itself

    This is atomic in Redis (single EVAL script).
    """
    await self._transport.delete_tags(*tags)
```

When caching a query result, we record its dependencies:

```python
async def cache_query_result(
    self,
    cache_key: str,
    result,
    models_pks: dict[type, list],  # {User: [42], Product: [1, 2]}
    ttl: int,
) -> None:
    """Cache a query result with automatic dependency tagging."""
    tags = []
    for model, pks in models_pks.items():
        tags.extend(generate_tags(model, pks))

    await self._transport.set(cache_key, result, expire=ttl, tags=tags)
```

### R5: Primary Key Extraction from Query Results

Extract PKs from cached query results to generate appropriate tags:

For single-row queries (`get`):

```python
def extract_pks_from_get_result(result, model: type) -> list:
    """Extract PK from a single row result."""
    if result is None:
        return []
    pk = extract_pk_from_instance(result)
    return [pk]
```

For multi-row queries (`fetch`):

```python
def extract_pks_from_fetch_result(results: list, models: list[type]) -> dict[type, list]:
    """Extract PKs from multi-row results."""
    pks_by_model = {}
    for result_row in results:
        # Determine which model this row is
        for model in models:
            if isinstance(result_row, model):
                pk = extract_pk_from_instance(result_row)
                pks_by_model.setdefault(model, []).append(pk)
                break

    return pks_by_model
```

For column-level results (e.g., `select(User.id, User.name)`):

```python
def extract_pk_from_column_result(row, model: type) -> int | None:
    """
    Extract PK from a row with only certain columns selected.

    Problem: If User.id is not in the select, we can't extract the PK.

    Solution (v0.1.0): Rewrite the query to include the PK column.
    Then extract and strip from final result before returning to user.
    """
    # Placeholder for v0.1.0 — inject PK during query interception
    # Covered in query-interception spec
    pass
```

### R6: Bulk Operation Handling (The Silent Killer)

ORM events like `after_insert`, `after_update`, `after_delete` fire per-instance. However, bulk operations bypass these:

```python
# This triggers after_update for each User:
for user in session.query(User):
    user.name = "updated"
    session.flush()  # ← fires after_update

# This does NOT trigger after_update:
session.execute(update(User).where(User.active == True).values(name="updated"))
session.commit()  # ← NO event fired
```

For v0.1.0, this is **documented as a known limitation**. Options:

A. **Raise exception on bulk operations** — Not practical, users expect bulk ops to work
B. **Fall back to table-level invalidation** — Hook `after_bulk_update`, `after_bulk_delete` and invalidate entire table
C. **Do nothing** — Document limitation, require manual invalidation for bulk ops

**Chosen for v0.1.0**: Option B + documentation. Hook `after_bulk_update` and `after_bulk_delete`, but invalidate the entire table (not individual rows):

```python
@event.listens_for(Mapper, "after_bulk_update", propagate=True)
def invalidate_after_bulk_update(update_context):
    """Invalidate entire table after bulk update."""
    model = update_context.mapper.class_
    # Cannot know which specific rows were updated, so use table-level invalidation
    # Invalidate all tags matching this table: "tablename:*"
    # For cashews, this requires a separate mechanism (no direct "wildcard delete")
    # Workaround: Store a "table-invalidation" counter or timestamp
    # Or: Use our own reverse index (deferred to v0.3.0)
```

**Workaround for v0.1.0**: Keep a separate "table version" for each model:

```python
# In cache storage
table_versions = {
    "users": 1,      # Version counter
    "products": 1,
}

# On bulk update:
await cache.increment(f"table_version:users")  # Bump version

# When caching a query result:
table_version = await cache.get(f"table_version:users")
cache_key = f"sqlacache:{query_hash}:v{table_version}"  # Include version in key
```

This way, bulk operations automatically invalidate all queries on that table (through version bump).

### R7: Public Invalidation API

Expose manual invalidation:

```python
class CacheManager:
    async def invalidate(self, model: type = None, pks: list = None):
        """
        Manually invalidate cache entries.

        Args:
            model: Model class to invalidate
            pks: List of PK values; if None, invalidate entire table

        Behavior:
            - invalidate(User, [42, 55]) → delete tags "users:42", "users:55"
            - invalidate(User) → bump table version for User
            - invalidate() → invalidate all tables (clear entire cache)
        """
        if model is None:
            # Invalidate all
            await self.invalidate_all()
        elif pks is None:
            # Table-level invalidation via version bump
            await self._bump_table_version(model.__tablename__)
        else:
            # Row-level invalidation
            tags = generate_tags(model, pks)
            await self._transport.delete_tags(*tags)

    async def invalidate_all(self):
        """Flush entire cache."""
        await self._transport.delete_all()  # or recreate cache instance
```

## Implementation Notes

- Use `sqlalchemy.inspect()` to get mapper info reliably
- Composite PKs must be stringified consistently (use Python's `repr()` or JSON)
- Ensure PK extraction happens **after flush** (events fire after flush, before commit)
- For the table-version workaround, store version counters in the cache itself (not external storage)
- Test edge cases: NULL PKs, UUID PKs, non-integer PKs

## Acceptance Criteria

- ✓ Tags generated correctly for single and composite PKs
- ✓ PKs extracted from ORM instances without error
- ✓ `after_insert`, `after_update`, `after_delete` hooks register and fire
- ✓ `delete_tags()` called with correct tags on write events
- ✓ Manual `invalidate()` API works for row-level and table-level invalidation
- ✓ Bulk operations documented as limitation or handled via table-version workaround
- ✓ cache-cached query results are tagged during `cache.set()`
- ✓ No PK extraction errors for common SQLAlchemy patterns
