# Spec: Cache Key Generation

Implement deterministic cache key generation from SQLAlchemy statements and bound parameters.

## Requirements

### R1: Deterministic Key Generation

Generate a cache key that:

1. Is **deterministic** — same statement + parameters always produce the same key
2. Is **unique** — different statements produce different keys with high probability
3. Is **short** — fits into Redis key size limits (typically 512MB max key size, but keep individual keys to KB range)
4. Is **parameterized** — includes bound parameter values in the key, not just SQL template

Example:

```python
stmt1 = select(User).where(User.id == 42)
stmt2 = select(User).where(User.id == 42)
stmt3 = select(User).where(User.id == 55)

# stmt1 and stmt2 should produce the SAME key
key1 = generate_cache_key(stmt1, {})
key2 = generate_cache_key(stmt2, {})
assert key1 == key2

# stmt3 should produce a DIFFERENT key
key3 = generate_cache_key(stmt3, {})
assert key3 != key1
```

### R2: SQL Serialization

Convert SQLAlchemy statement to a normalized SQL string:

```python
def statement_to_sql(stmt) -> str:
    """
    Compile statement to SQL string with bound parameters resolved.

    Warning: This is NOT SQL injection safe. Do not use raw SQL directly.
    Process: SQLAlchemy compiler → literal_binds to resolve parameters.
    """
    from sqlalchemy import text
    from sqlalchemy.dialects import sqlite  # or the deployed DB dialect

    # Compile with literal_binds to embed parameter values
    compiled = stmt.compile(compile_kwargs={"literal_binds": True}, dialect=sqlite.dialect())
    return str(compiled)
```

Issue: `literal_binds` requires a specific SQL dialect. Use the dialect of the bound engine if available, falling back to SQLite for caching purposes.

### R3: Hash Function

Hash the normalized SQL string to a fixed-size key:

```python
import hashlib

def sql_to_cache_key(sql_str: str, prefix: str = "sqlacache") -> str:
    """
    Hash SQL string to cache key.

    Example:
        "SELECT user.id FROM user WHERE user.id = 42"
        → "sqlacache:5f8a3b2c1e9d4a7b6f0c3e2a1b9d8f4"
    """
    digest = hashlib.sha256(sql_str.encode()).hexdigest()[:16]  # Use first 16 hex chars
    return f"{prefix}:{digest}"
```

Use SHA-256 (any cryptographic hash) and truncate to 16–32 hex characters for brevity. This gives ~64–128 bits of entropy, collision risk is negligible for cache key purposes.

### R4: Integration with Statement Compiler

Flow:

```python
def generate_cache_key(stmt, params: dict = None, prefix: str = "sqlacache") -> str:
    """
    Generate deterministic cache key from SQLAlchemy statement and parameters.

    Args:
        stmt: SQLAlchemy Select statement (not raw text SQL)
        params: Bound parameters (usually empty for ORM queries)
        prefix: Cache key prefix (from config)

    Returns:
        Cache key string suitable for Redis/cache storage
    """
    # Normalize SQL
    sql = statement_to_sql(stmt)

    # If params dict is not empty, append as JSON
    if params:
        import json
        params_str = json.dumps(params, sort_keys=True, default=str)
        sql = f"{sql}|{params_str}"

    # Hash to key
    return sql_to_cache_key(sql, prefix=prefix)
```

### R5: Handling Complex Queries

For queries with:

- **Joins**: Include all joined table conditions in normalization
- **Subqueries**: Inline or reference by subquery hash
- **Column-level selects**: Include which columns are selected (e.g., `select(User.id, User.name)` vs `select(User)`)
- **Ordering/Filtering**: Include ORDER BY, WHERE clauses

The normalized SQL from SQLAlchemy's compiler automatically includes these, so no special handling needed.

### R6: Parameter Binding Resolution

For parameterized queries:

```python
# Example: SQLAlchemy auto-parameterizes
stmt = select(User).where(User.id == 42)  # SQLAlchemy binds 42 as a parameter

# Compiled with literal_binds:
# "SELECT user.id, user.name FROM user WHERE user.id = 42"

# The value 42 is now part of the SQL string, so cache keys are parameter-aware
```

For queries with multiple parameter values (e.g., `func.now()`), ensure they're resolved deterministically:

```python
# This should fail or be handled explicitly:
stmt = select(User).order_by(func.now())

# Because func.now() changes every execution, the cache key would differ
# Solution: Explicitly exclude non-deterministic functions, or cache misses occur naturally
```

### R7: Configuration Prefix

Cache keys should be prepended with a configurable prefix (default `"sqlacache"`):

```python
# From config
prefix = cache_config.get("prefix", "sqlacache")
cache_key = generate_cache_key(stmt, prefix=prefix)

# In Redis: "sqlacache:5f8a3b2c1e9d4a7b6f0c..."
# This prevents collisions if the same Redis instance is shared
```

### R8: Caching Strategy for Keys

Store the `generate_cache_key` function in `src/sqlacache/utils/key_generation.py`:

```python
# src/sqlacache/utils/key_generation.py

def generate_cache_key(statement, prefix: str = "sqlacache") -> str:
    """Public function imported by interceptor."""
    ...

def statement_to_sql(statement) -> str:
    """Internal helper."""
    ...

def sql_to_cache_key(sql: str, prefix: str = "sqlacache") -> str:
    """Internal helper."""
    ...
```

## Implementation Notes

- Use SQLAlchemy's built-in compiler to normalize SQL (don't write custom SQL parser)
- `literal_binds=True` ensures parameters are embedded in the SQL string
- For edge cases (window functions, CTEs), test that SQLAlchemy's compiler generates consistent output
- Consider caching the SQL compilation step for repeated queries (optional, v0.2.0 optimization)

## Acceptance Criteria

- ✓ Same statement produces same cache key
- ✓ Different statements produce different cache keys
- ✓ Cache keys are 32–64 characters (shorter is better)
- ✓ Cache keys include parameter values (parameterized queries produce different keys)
- ✓ Prefix is applied correctly
- ✓ `generate_cache_key()` is exported from `src/sqlacache/utils/key_generation.py`
- ✓ No exceptions raised for simple and moderately complex queries
- ✓ Performance: generating a key should be <1ms for typical queries
