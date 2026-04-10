"""Comprehensive tests for sqlacache.serializers module."""

from __future__ import annotations

import json

from sqlacache.serializers.json import ModelJSONSerializer

from .conftest import CompositeRecord, User


class TestModelJSONSerializer:
    def setup_method(self) -> None:
        self.serializer = ModelJSONSerializer()

    def test_serialize_single_instance(self) -> None:
        user = User(id=1, name="alice")
        result = self.serializer.serialize(user)
        parsed = json.loads(result)
        assert parsed["id"] == 1
        assert parsed["name"] == "alice"

    def test_serialize_list_of_instances(self) -> None:
        users = [User(id=1, name="alice"), User(id=2, name="bob")]
        result = self.serializer.serialize(users)
        parsed = json.loads(result)
        assert len(parsed) == 2
        assert parsed[0]["id"] == 1
        assert parsed[1]["id"] == 2

    def test_serialize_empty_list(self) -> None:
        result = self.serializer.serialize([])
        assert json.loads(result) == []

    def test_serialize_sorts_keys(self) -> None:
        user = User(id=1, name="alice")
        result = self.serializer.serialize(user)
        keys = list(json.loads(result).keys())
        assert keys == sorted(keys)

    def test_serialize_composite_pk_model(self) -> None:
        record = CompositeRecord(tenant_id=1, user_id=2, label="test")
        result = self.serializer.serialize(record)
        parsed = json.loads(result)
        assert parsed["tenant_id"] == 1
        assert parsed["user_id"] == 2
        assert parsed["label"] == "test"

    def test_deserialize_returns_dict(self) -> None:
        payload = '{"id": 1, "name": "alice"}'
        result = self.serializer.deserialize(payload)
        assert result == {"id": 1, "name": "alice"}

    def test_deserialize_list(self) -> None:
        payload = '[{"id": 1}, {"id": 2}]'
        result = self.serializer.deserialize(payload)
        assert result == [{"id": 1}, {"id": 2}]

    def test_roundtrip(self) -> None:
        user = User(id=42, name="roundtrip")
        serialized = self.serializer.serialize(user)
        deserialized = self.serializer.deserialize(serialized)
        assert deserialized["id"] == 42
        assert deserialized["name"] == "roundtrip"
