"""Adapter contract checks, NOT a live Milvus verification."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from urban_mobility_rag.models import Chunk
from urban_mobility_rag.store import MilvusVectorStore


def test_milvus_metadata_roundtrip_and_filtered_search():
    store = object.__new__(MilvusVectorStore)
    store.client = Mock()
    store.collection_name = "test_v2"
    metadata = {
        "audience": "passenger",
        "city": "上海市",
        "valid_until": "2026-10-20",
        "data_kind": "real",
        "source_url": "https://example.org/notice",
        "version": "2",
    }
    chunk = Chunk("c1", "doc", "title", "specific supported statement", "source", 0, metadata, [1.0, 0.0])
    store.upsert([chunk])
    assert store.client.upsert.call_args.args[1][0]["metadata"] == metadata
    entity = chunk.to_dict()
    store.client.search.return_value = [[{"entity": entity, "id": "c1", "distance": 0.9}]]
    hit = store.search([1.0, 0.0], allowed_ids={"c1"})[0]
    assert hit.chunk.metadata == metadata
    assert store.client.search.call_args.kwargs["filter"] == 'chunk_id in ["c1"]'
    assert "metadata" in store.client.search.call_args.kwargs["output_fields"]


def test_old_schema_stops_without_drop():
    client = Mock()
    client.has_collection.return_value = True
    client.describe_collection.return_value = {"fields": [{"name": "vector", "params": {"dim": 2}}]}
    module = SimpleNamespace(DataType=Mock(), MilvusClient=Mock(return_value=client))
    with patch.dict(sys.modules, {"pymilvus": module}):
        with pytest.raises(RuntimeError, match="NEW versioned"):
            MilvusVectorStore("http://localhost:19530", "", "legacy", 2)
    client.drop_collection.assert_not_called()
    client.delete.assert_not_called()
