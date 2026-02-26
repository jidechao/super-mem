"""Milvus vector storage layer using MilvusClient API."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MilvusStore:
    """Thin wrapper around ``pymilvus.MilvusClient`` for chunk storage.

    Collections use both dense vector and BM25 sparse vector fields,
    with hybrid search (semantic + keyword, RRF reranking) by default.
    """

    DEFAULT_COLLECTION = "memsearch_chunks"

    def __init__(
        self,
        uri: str = "~/.memsearch/milvus.db",
        *,
        token: str | None = None,
        collection: str = DEFAULT_COLLECTION,
        dimension: int | None = 1536,
    ) -> None:
        from pymilvus import MilvusClient

        resolved = str(Path(uri).expanduser()) if not uri.startswith(("http", "tcp")) else uri
        if not uri.startswith(("http", "tcp")):
            Path(resolved).parent.mkdir(parents=True, exist_ok=True)
        resolved_token = token or os.environ.get("MILVUS_TOKEN", "")
        connect_kwargs: dict[str, Any] = {"uri": resolved}
        if resolved_token:
            connect_kwargs["token"] = resolved_token
        self._client = MilvusClient(**connect_kwargs)
        self._collection = collection
        self._dimension = dimension
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        if self._client.has_collection(self._collection):
            self._check_dimension()
            return

        if self._dimension is None:
            return  # read-only mode: don't create a new collection

        from pymilvus import DataType, Function, FunctionType

        schema = self._client.create_schema(enable_dynamic_field=True)
        schema.add_field(field_name="chunk_hash", datatype=DataType.VARCHAR, max_length=64, is_primary=True)
        schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=self._dimension)
        schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=65535, enable_analyzer=True)
        schema.add_field(field_name="sparse_vector", datatype=DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field(field_name="source", datatype=DataType.VARCHAR, max_length=1024)
        schema.add_field(field_name="heading", datatype=DataType.VARCHAR, max_length=1024)
        schema.add_field(field_name="heading_level", datatype=DataType.INT64)
        schema.add_field(field_name="start_line", datatype=DataType.INT64)
        schema.add_field(field_name="end_line", datatype=DataType.INT64)
        schema.add_field(field_name="user_id", datatype=DataType.VARCHAR, max_length=128, default_value="")
        schema.add_function(Function(
            name="bm25_fn",
            function_type=FunctionType.BM25,
            input_field_names=["content"],
            output_field_names=["sparse_vector"],
        ))

        index_params = self._client.prepare_index_params()
        index_params.add_index(field_name="embedding", index_type="FLAT", metric_type="COSINE")
        index_params.add_index(field_name="sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")

        self._client.create_collection(
            collection_name=self._collection,
            schema=schema,
            index_params=index_params,
        )

    def _check_dimension(self) -> None:
        """Verify that the existing collection's embedding dimension matches."""
        if self._dimension is None:
            return  # no dimension specified — skip check (read-only mode)
        try:
            info = self._client.describe_collection(self._collection)
        except Exception:
            return  # best-effort; skip if describe is not supported
        for field in info.get("fields", []):
            if field.get("name") == "embedding":
                existing_dim = field.get("params", {}).get("dim")
                if existing_dim is not None and int(existing_dim) != self._dimension:
                    raise ValueError(
                        f"Embedding dimension mismatch: collection '{self._collection}' "
                        f"has dim={existing_dim} but the current embedding provider "
                        f"outputs dim={self._dimension}. "
                        f"Run 'memsearch reset --yes' to drop the collection and re-index, "
                        f"or use a different --milvus-uri / --collection."
                    )
                break

    def existing_hashes(self, hashes: list[str], *, user_id: str = "") -> set[str]:
        """Return the subset of *hashes* that already exist in the collection."""
        if not hashes:
            return set()
        hash_list = ", ".join(f'"{h}"' for h in hashes)
        filter_expr = self._build_filter(f"chunk_hash in [{hash_list}]", user_id)
        results = self._client.query(
            collection_name=self._collection,
            filter=filter_expr,
            output_fields=["chunk_hash"],
        )
        return {r["chunk_hash"] for r in results}

    def upsert(self, chunks: list[dict[str, Any]], *, user_id: str = "") -> int:
        """Insert or update chunks (keyed by ``chunk_hash`` primary key).

        ``sparse_vector`` is auto-generated by the BM25 Function from
        ``content`` — do NOT include it in chunk dicts.
        """
        if not chunks:
            return 0
        normalized_chunks: list[dict[str, Any]] = []
        for chunk in chunks:
            if "user_id" in chunk:
                normalized_chunks.append(chunk)
            else:
                normalized_chunks.append({**chunk, "user_id": user_id})
        result = self._client.upsert(
            collection_name=self._collection,
            data=normalized_chunks,
        )
        return result.get("upsert_count", len(chunks)) if isinstance(result, dict) else len(chunks)

    def search(
        self,
        query_embedding: list[float],
        *,
        query_text: str = "",
        top_k: int = 10,
        filter_expr: str = "",
        user_id: str = "",
    ) -> list[dict[str, Any]]:
        """Hybrid search: dense vector + BM25 full-text with RRF reranking."""
        from pymilvus import AnnSearchRequest, RRFRanker

        req_kwargs: dict[str, Any] = {
            "expr": self._build_filter(filter_expr, user_id),
        }

        dense_req = AnnSearchRequest(
            data=[query_embedding],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {}},
            limit=top_k,
            **req_kwargs,
        )

        bm25_req = AnnSearchRequest(
            data=[query_text] if query_text else [""],
            anns_field="sparse_vector",
            param={"metric_type": "BM25"},
            limit=top_k,
            **req_kwargs,
        )

        results = self._client.hybrid_search(
            collection_name=self._collection,
            reqs=[dense_req, bm25_req],
            ranker=RRFRanker(k=60),
            limit=top_k,
            output_fields=self._QUERY_FIELDS,
        )

        if not results or not results[0]:
            return []
        return [
            {**hit["entity"], "score": hit["distance"]}
            for hit in results[0]
        ]

    _QUERY_FIELDS = [
        "content", "source", "heading", "chunk_hash",
        "heading_level", "start_line", "end_line", "user_id",
    ]

    def query(self, *, filter_expr: str = "", user_id: str = "") -> list[dict[str, Any]]:
        """Retrieve chunks by scalar filter (no vector needed)."""
        kwargs: dict[str, Any] = {
            "collection_name": self._collection,
            "output_fields": self._QUERY_FIELDS,
            "filter": self._build_filter(filter_expr, user_id),
        }
        return self._client.query(**kwargs)

    def hashes_by_source(self, source: str, *, user_id: str = "") -> set[str]:
        """Return all chunk_hash values for a given source file."""
        filter_expr = self._build_filter(f'source == "{self._escape_filter_literal(source)}"', user_id)
        results = self._client.query(
            collection_name=self._collection,
            filter=filter_expr,
            output_fields=["chunk_hash"],
        )
        return {r["chunk_hash"] for r in results}

    def indexed_sources(self, *, user_id: str = "") -> set[str]:
        """Return all distinct source values in the collection."""
        results = self._client.query(
            collection_name=self._collection,
            filter=self._build_filter("", user_id),
            output_fields=["source"],
        )
        return {r["source"] for r in results}

    def delete_by_source(self, source: str, *, user_id: str = "") -> None:
        """Delete all chunks from a given source file."""
        filter_expr = self._build_filter(f'source == "{self._escape_filter_literal(source)}"', user_id)
        self._client.delete(
            collection_name=self._collection,
            filter=filter_expr,
        )

    def delete_by_hashes(self, hashes: list[str]) -> None:
        """Delete chunks by their content hashes (primary keys)."""
        if not hashes:
            return
        self._client.delete(
            collection_name=self._collection,
            ids=hashes,
        )

    def count(self, *, user_id: str = "") -> int:
        """Return total number of stored chunks."""
        if user_id:
            return len(self.query(user_id=user_id))
        stats = self._client.get_collection_stats(self._collection)
        return stats.get("row_count", 0)

    def _build_filter(self, filter_expr: str, user_id: str) -> str:
        """Merge optional user filter with custom Milvus filter expression."""
        parts: list[str] = []
        if user_id:
            safe_user = self._escape_filter_literal(user_id)
            parts.append(f'user_id == "{safe_user}"')
        if filter_expr:
            parts.append(f"({filter_expr})")
        if not parts:
            return 'chunk_hash != ""'
        return " and ".join(parts)

    def _escape_filter_literal(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def drop(self) -> None:
        """Drop the entire collection."""
        if self._client.has_collection(self._collection):
            self._client.drop_collection(self._collection)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> MilvusStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
