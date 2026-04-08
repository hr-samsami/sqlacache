"""Model-aware JSON serialization helpers."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import inspect


class ModelJSONSerializer:
    """Serialize ORM model instances using column attributes only."""

    def serialize(self, obj: Any) -> str:
        if isinstance(obj, list):
            return json.dumps([self._instance_to_dict(item) for item in obj], default=str, sort_keys=True)
        return json.dumps(self._instance_to_dict(obj), default=str, sort_keys=True)

    def deserialize(self, payload: str) -> Any:
        return json.loads(payload)

    @staticmethod
    def _instance_to_dict(instance: Any) -> dict[str, Any]:
        return {attr.key: getattr(instance, attr.key) for attr in inspect(instance).mapper.column_attrs}
