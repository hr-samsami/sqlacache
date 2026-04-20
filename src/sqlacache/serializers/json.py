"""Model-aware JSON serialization helpers."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import inspect


class ModelJSONEncoder:
    """Encode ORM model instances to JSON using their column attributes.

    This is one-way by design: ``encode(instance) -> json_string`` and
    ``decode(json_string) -> dict``. Reconstructing mapped instances from the
    decoded dict requires a SQLAlchemy session and knowledge of the target
    mapper, which callers are better positioned to do than we are. If you
    need round-trip fidelity, cache the frozen SQLAlchemy result instead
    (what the default serializer does).
    """

    def encode(self, obj: Any) -> str:
        """Serialize an ORM instance (or list of instances) to JSON."""

        if isinstance(obj, list):
            return json.dumps([self._instance_to_dict(item) for item in obj], default=str, sort_keys=True)
        return json.dumps(self._instance_to_dict(obj), default=str, sort_keys=True)

    def decode(self, payload: str) -> Any:
        """Deserialize a JSON payload to a plain dict (or list of dicts).

        Does not reconstruct mapped instances — the caller is responsible for
        rehydrating if needed.
        """

        return json.loads(payload)

    @staticmethod
    def _instance_to_dict(instance: Any) -> dict[str, Any]:
        return {attr.key: getattr(instance, attr.key) for attr in inspect(instance).mapper.column_attrs}


class ModelJSONSerializer(ModelJSONEncoder):
    """Deprecated alias for :class:`ModelJSONEncoder`.

    The ``serialize``/``deserialize`` names implied a round-trip that this
    helper doesn't provide — ``deserialize`` returns a dict, not an instance.
    Use :class:`ModelJSONEncoder` with its ``encode``/``decode`` methods
    instead. This alias remains for backwards compatibility.
    """

    def serialize(self, obj: Any) -> str:
        return self.encode(obj)

    def deserialize(self, payload: str) -> Any:
        return self.decode(payload)
