from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from pathlib import Path

from langchain_core.documents import Document
from qdrant_client import QdrantClient, models


class QdrantVectorStore:
    def __init__(self, settings, embedding_service):
        self.settings = settings
        self.embedding_service = embedding_service
        self.collection_name = settings.QDRANT_COLLECTION_NAME
        use_cloud = settings.QDRANT_MODE == "cloud" or (
            settings.QDRANT_MODE == "auto" and bool(settings.QDRANT_URL)
        )
        if use_cloud:
            if not settings.QDRANT_URL:
                raise ValueError("QDRANT_URL is required when QDRANT_MODE=cloud.")
            self.client = QdrantClient(
                url=settings.QDRANT_URL,
                api_key=settings.QDRANT_API_KEY,
            )
        else:
            self.storage_path = Path(settings.resolved_qdrant_path)
            self.storage_path.mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=str(self.storage_path))
        self.vector_size = self.embedding_service.dimension
        self.ensure_collection()

    def ensure_collection(self) -> None:
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=self.vector_size,
                    distance=models.Distance.COSINE,
                ),
            )
            return

        collection = self.client.get_collection(self.collection_name)
        params = getattr(collection.config, "params", None)
        vectors = getattr(params, "vectors", None)
        if vectors is not None:
            size = getattr(vectors, "size", None)
            if size is not None and size != self.vector_size:
                raise ValueError(
                    f"Qdrant collection '{self.collection_name}' uses dimension {size}, but the configured model expects {self.vector_size}."
                )

    def drop_collection(self) -> None:
        if self.client.collection_exists(self.collection_name):
            self.client.delete_collection(self.collection_name)
        self.ensure_collection()

    @staticmethod
    def _point_id_for(doc: Document, index: int) -> str:
        source = doc.metadata.get("document_name") or doc.metadata.get("source_file") or "unknown.pdf"
        page = doc.metadata.get("page", 1) or 1
        document_hash = doc.metadata.get("document_hash") or ""
        candidate = f"{source}:{page}:{index}:{document_hash}"
        digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
        return str(uuid.UUID(hex=digest[:32]))

    def upsert_documents(self, documents: Sequence[Document], reset: bool = False) -> dict:
        if not documents:
            return {"indexed": 0}

        valid_documents = [doc for doc in documents if doc.page_content and doc.page_content.strip()]
        if not valid_documents:
            return {"indexed": 0}

        texts = [doc.page_content.strip() for doc in valid_documents]
        vectors = self.embedding_service.embed_documents(texts)
        points = []

        for index, doc in enumerate(valid_documents):
            serialized = dict(doc.metadata or {})
            payload = {
                "document": serialized.get("document_name") or serialized.get("source_file") or "unknown.pdf",
                "page": int(serialized.get("page", 1) or 1),
                "chunk_id": serialized.get("chunk_id") or self._point_id_for(doc, index),
                "text": doc.page_content.strip(),
                "document_hash": serialized.get("document_hash"),
                "source_file": serialized.get("source_file"),
            }
            payload.update(serialized)
            points.append(
                models.PointStruct(
                    id=payload["chunk_id"],
                    vector=vectors[index],
                    payload=payload,
                )
            )

        if points:
            if reset:
                self.drop_collection()
            self.client.upsert(collection_name=self.collection_name, points=points)
            current_hash_by_source = {
                str(point.payload["source_file"]): point.payload["document_hash"]
                for point in points
                if point.payload.get("source_file") and point.payload.get("document_hash")
            }
            for source_file, document_hash in current_hash_by_source.items():
                self.client.delete(
                    collection_name=self.collection_name,
                    points_selector=models.FilterSelector(
                        filter=models.Filter(
                            must=[
                                models.FieldCondition(
                                    key="source_file",
                                    match=models.MatchValue(value=source_file),
                                )
                            ],
                            must_not=[
                                models.FieldCondition(
                                    key="document_hash",
                                    match=models.MatchValue(value=document_hash),
                                )
                            ],
                        )
                    ),
                )

        return {"indexed": len(points)}

    def search(self, query_vector: list[float], limit: int = 4) -> list[dict]:
        hits = self.client.search(
            collection_name=self.collection_name,
            query_vector=query_vector,
            limit=limit,
            with_payload=True,
        )
        return [
            {"id": hit.id, "score": float(hit.score), "payload": hit.payload or {}}
            for hit in hits
        ]
