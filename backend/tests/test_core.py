from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document

from app.config import Settings
from app.services.document_service import DocumentService
from app.services.document_revision import classify_revisions, inspect_revision
from app.services.ingestion_service import IngestionService
from app.services.rag_service import RAGService, UNKNOWN_ANSWER
from app.services.vector_store import QdrantVectorStore


class FakeEmbeddingService:
    def __init__(self, dimension=3):
        self.dimension = dimension

    def embed_documents(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, question):
        return [1.0, 0.0, 0.0]


class FakeQdrantClient:
    def __init__(self, *args, **kwargs):
        self.collections = {}
        self.deleted_selectors = []

    def collection_exists(self, name):
        return name in self.collections

    def create_collection(self, collection_name, vectors_config):
        self.collections[collection_name] = {"config": SimpleNamespace(params=SimpleNamespace(vectors=SimpleNamespace(size=vectors_config.size)))}

    def get_collection(self, name):
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=SimpleNamespace(size=3))))

    def upsert(self, collection_name, points):
        self.collections.setdefault(collection_name, {})
        self.collections[collection_name]["points"] = list(points)

    def delete(self, collection_name, points_selector):
        self.deleted_selectors.append((collection_name, points_selector))

    def search(self, collection_name, query_vector, limit, with_payload):
        return [
            SimpleNamespace(
                id="chunk-1",
                score=0.82,
                payload={
                    "document": "sample.pdf",
                    "page": 3,
                    "text": "Follow the safety lockout procedure before service work.",
                },
            )
        ]


@pytest.fixture
def settings():
    return Settings(
        GEMINI_API_KEY="test-key",
        HUGGINGFACE_API_KEY="hf-demo-key",
        QDRANT_URL=None,
        QDRANT_API_KEY=None,
        QDRANT_STORAGE_PATH="./storage/qdrant_db",
        QDRANT_COLLECTION_NAME="test_collection",
        DOCUMENT_STORAGE_PATH="./storage/documents",
        SUPABASE_URL=None,
        SUPABASE_SERVICE_ROLE_KEY=None,
        SUPABASE_CACHE_PATH="./storage/supabase_cache",
        CHUNK_SIZE=200,
        CHUNK_OVERLAP=50,
        TOP_K=4,
        RETRIEVAL_THRESHOLD=0.5,
    )


def test_missing_hf_key_raises(monkeypatch):
    monkeypatch.delenv("HUGGINGFACE_API_KEY", raising=False)
    settings = Settings(HUGGINGFACE_API_KEY=None, HF_EMBEDDING_PROVIDER="api")
    with pytest.raises(ValueError):
        from app.services.embedding_service import EmbeddingService

        EmbeddingService(settings)


def test_embedding_dimension_is_available_without_api_call(monkeypatch, settings):
    settings.HF_EMBEDDING_PROVIDER = "api"

    class FakeInferenceClient:
        def __init__(self, **kwargs):
            self.options = kwargs

    monkeypatch.setattr("app.services.embedding_service.InferenceClient", FakeInferenceClient)
    from app.services.embedding_service import EmbeddingService

    embeddings = EmbeddingService(settings)
    assert embeddings.dimension == 384


def test_embedding_service_rejects_unexpected_vector_size(monkeypatch, settings):
    settings.HF_EMBEDDING_PROVIDER = "api"

    class FakeInferenceClient:
        def __init__(self, **kwargs):
            pass

        def feature_extraction(self, text):
            return [[0.1, 0.2]]

    monkeypatch.setattr("app.services.embedding_service.InferenceClient", FakeInferenceClient)
    from app.services.embedding_service import EmbeddingService

    with pytest.raises(ValueError, match="expected 384 dimensions"):
        EmbeddingService(settings).embed_query("dimension-check")


def test_embedding_service_pools_token_vectors_and_normalizes(monkeypatch, settings):
    settings.HF_EMBEDDING_PROVIDER = "api"

    class FakeInferenceClient:
        def __init__(self, **kwargs):
            self.options = kwargs

        def feature_extraction(self, text):
            assert text == "first second"
            return [[[1.0, 0.0], [0.0, 1.0]]]

    settings.HF_EMBEDDING_DIMENSION = 2
    monkeypatch.setattr("app.services.embedding_service.InferenceClient", FakeInferenceClient)
    from app.services.embedding_service import EmbeddingService

    vector = EmbeddingService(settings).embed_query("first\nsecond")
    assert vector == pytest.approx([2**-0.5, 2**-0.5])


def test_local_embedding_provider_does_not_require_api_key(monkeypatch, settings):
    settings.HUGGINGFACE_API_KEY = None
    settings.HF_EMBEDDING_PROVIDER = "local"
    settings.HF_EMBEDDING_DIMENSION = 3
    model_options = {}

    class FakeSentenceTransformer:
        def __init__(self, model_name, token=None):
            model_options["model_name"] = model_name
            model_options["token"] = token

        def encode(self, texts, **kwargs):
            model_options["texts"] = texts
            model_options["encode_options"] = kwargs
            return [[3.0, 4.0, 0.0]]

    monkeypatch.setattr(
        "app.services.embedding_service._load_local_sentence_transformer",
        lambda model_name, *, token=None: FakeSentenceTransformer(model_name, token),
    )
    from app.services.embedding_service import EmbeddingService

    vector = EmbeddingService(settings).embed_query("machine\nsafety")

    assert vector == pytest.approx([0.6, 0.8, 0.0])
    assert model_options["model_name"] == settings.HF_EMBEDDING_MODEL
    assert model_options["token"] is None
    assert model_options["texts"] == ["machine safety"]


def test_local_embedding_provider_uses_configured_huggingface_token(monkeypatch, settings):
    settings.HF_EMBEDDING_PROVIDER = "local"
    settings.HF_EMBEDDING_DIMENSION = 3
    captured = {}

    class FakeSentenceTransformer:
        def encode(self, texts, **kwargs):
            return [[3.0, 4.0, 0.0]]

    def load_model(model_name, *, token=None):
        captured.update(model_name=model_name, token=token)
        return FakeSentenceTransformer()

    monkeypatch.setattr(
        "app.services.embedding_service._load_local_sentence_transformer",
        load_model,
    )
    from app.services.embedding_service import EmbeddingService

    service = EmbeddingService(settings)
    service.embed_query("authenticated local model download")

    assert captured == {
        "model_name": settings.HF_EMBEDDING_MODEL,
        "token": settings.HUGGINGFACE_API_KEY,
    }


def test_rag_service_configures_gemini(monkeypatch, settings):
    from app.services import rag_service

    captured = {}

    class FakeGemini:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(rag_service, "ChatGoogleGenerativeAI", FakeGemini)

    rag_service.RAGService(settings, FakeEmbeddingService(), FakeQdrantClient())

    assert captured["model"] == settings.GEMINI_MODEL
    assert captured["api_key"] == settings.GEMINI_API_KEY
    assert captured["request_timeout"] == settings.GEMINI_TIMEOUT_SECONDS


def test_rag_service_requires_gemini_key(settings):
    settings.GEMINI_API_KEY = None
    from app.services.rag_service import RAGService

    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        RAGService(settings, FakeEmbeddingService(), FakeQdrantClient())


def test_hosted_embedding_payment_error_is_actionable():
    from fastapi import HTTPException
    from huggingface_hub.errors import HfHubHTTPError
    from httpx import Request, Response

    import app.main as main

    error = HfHubHTTPError(
        "payment required",
        response=Response(402, request=Request("GET", "https://example.test")),
    )

    response = main.hosted_embedding_error(error)

    assert isinstance(response, HTTPException)
    assert response.status_code == 503
    assert "valid API key does not include inference credits" in response.detail


def test_gemini_invalid_key_error_is_actionable():
    from fastapi import HTTPException
    from google.api_core.exceptions import Unauthorized

    import app.main as main

    result = main.gemini_provider_error(Unauthorized("invalid key"))

    assert isinstance(result, HTTPException)
    assert result.status_code == 503
    assert "GEMINI_API_KEY" in result.detail


def test_qdrant_collection_initializes_and_upserts(monkeypatch, settings):
    monkeypatch.setattr(
        "app.services.vector_store.QdrantClient",
        FakeQdrantClient,
    )
    vector_store = QdrantVectorStore(settings, FakeEmbeddingService())
    doc = Document(page_content="Follow the safety lockout procedure before maintenance.", metadata={"document_name": "sample.pdf", "page": 3, "document_hash": "abc"})
    result = vector_store.upsert_documents([doc], reset=False)
    assert result["indexed"] == 1
    hits = vector_store.search([1.0, 0.0, 0.0], limit=1)
    assert hits[0]["payload"]["document"] == "sample.pdf"


def test_qdrant_replaces_stale_chunks_when_document_changes(monkeypatch, settings):
    client = FakeQdrantClient()

    class FakeClient(FakeQdrantClient):
        def __init__(self, *args, **kwargs):
            super().__init__()
            self.deleted_selectors = client.deleted_selectors

        def upsert(self, collection_name, points):
            client.upsert(collection_name, points)

        def delete(self, collection_name, points_selector):
            client.delete(collection_name, points_selector)

    monkeypatch.setattr("app.services.vector_store.QdrantClient", FakeClient)
    vector_store = QdrantVectorStore(settings, FakeEmbeddingService())
    document = Document(
        page_content="Updated procedure.",
        metadata={
            "document_name": "sample.pdf",
            "source_file": "sample.pdf",
            "page": 1,
            "document_hash": "new-hash",
        },
    )

    vector_store.upsert_documents([document])

    assert len(client.deleted_selectors) == 1
    _, selector = client.deleted_selectors[0]
    assert selector.filter.must[0].key == "source_file"
    assert selector.filter.must_not[0].key == "document_hash"


def test_local_qdrant_persists_only_latest_version_of_changed_source(settings, tmp_path):
    settings.QDRANT_STORAGE_PATH = str(tmp_path / "qdrant")
    vector_store = QdrantVectorStore(settings, FakeEmbeddingService())
    old_document = Document(
        page_content="Old procedure.",
        metadata={"document_name": "sample.pdf", "source_file": "sample.pdf", "page": 1, "document_hash": "old"},
    )
    current_document = Document(
        page_content="Current procedure.",
        metadata={"document_name": "sample.pdf", "source_file": "sample.pdf", "page": 1, "document_hash": "current"},
    )

    vector_store.upsert_documents([old_document])
    vector_store.upsert_documents([current_document])

    hits = vector_store.search([1.0, 0.0, 0.0], limit=10)
    assert len(hits) == 1
    assert hits[0]["payload"]["document_hash"] == "current"


def test_document_service_converts_pdf_page_index_to_human_page_number(monkeypatch, settings, tmp_path):
    pdf_path = tmp_path / "machine-manual.pdf"
    pdf_path.write_bytes(b"test pdf bytes")
    settings.DOCUMENT_STORAGE_PATH = str(tmp_path)

    class FakePDFLoader:
        def __init__(self, path):
            self.path = path

        def load(self):
            return [
                Document(page_content="Section one", metadata={"page": 0, "section": "Section 1"}),
                Document(page_content="Section two", metadata={"page": 1, "section": "Section 2"}),
            ]

    monkeypatch.setattr("app.services.document_service.PyMuPDFLoader", FakePDFLoader)
    pages, _ = DocumentService(settings).load_documents()

    assert [page.metadata["page"] for page in pages] == [1, 2]
    assert [page.metadata["section"] for page in pages] == ["Section 1", "Section 2"]


def test_document_service_extracts_text_from_a_real_pdf(settings, tmp_path):
    import pymupdf

    pdf_path = tmp_path / "QC-SOP-014-Rev04.pdf"
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Revision: Rev04 - July 1, 2026\nIncoming material checks")
    pdf.save(pdf_path)
    pdf.close()
    settings.DOCUMENT_STORAGE_PATH = str(tmp_path)

    pages, files = DocumentService(settings).load_documents()

    assert files == [pdf_path]
    assert len(pages) == 1
    assert "Incoming material checks" in pages[0].page_content
    assert pages[0].metadata["page"] == 1
    assert pages[0].metadata["revision"] == "04"


def test_document_service_syncs_supabase_pdfs_and_does_not_fallback_for_empty_bucket(
    monkeypatch, settings, tmp_path
):
    settings.SUPABASE_URL = "https://example.supabase.co"
    settings.SUPABASE_SERVICE_ROLE_KEY = "test-service-key"
    settings.DOCUMENT_STORAGE_PATH = str(tmp_path / "local")
    settings.SUPABASE_CACHE_PATH = str(tmp_path / "cache")
    local_path = tmp_path / "local"
    local_path.mkdir()
    (local_path / "local.pdf").write_bytes(b"local PDF")
    bucket_state = {
        "content": b"remote PDF",
        "updated_at": "2026-01-01T00:00:00Z",
        "download_count": 0,
    }

    class FakeBucket:
        def list(self, path=""):
            assert path == ""
            return [{
                "name": "remote.pdf",
                "updated_at": bucket_state["updated_at"],
                "metadata": {"size": len(bucket_state["content"])},
            }] if bucket_state["content"] is not None else []

        def download(self, name):
            assert name == "remote.pdf"
            bucket_state["download_count"] += 1
            return bucket_state["content"]

    bucket = FakeBucket()

    class FakeStorage:
        def from_(self, name):
            assert name == "files"
            return bucket

    class FakeClient:
        storage = FakeStorage()

    monkeypatch.setattr("app.services.document_service.create_client", lambda url, key: FakeClient())
    service = DocumentService(settings)

    synced_files = service.list_pdf_files()
    assert [path.name for path in synced_files] == ["remote.pdf"]
    assert synced_files[0].read_bytes() == b"remote PDF"
    assert service.get_storage_status()["status"] == "supabase"
    service.list_pdf_files()
    assert bucket_state["download_count"] == 1

    bucket_state["content"] = b"updated remote PDF"
    bucket_state["updated_at"] = "2026-02-01T00:00:00Z"
    changed_file = service.list_pdf_files()[0]
    assert changed_file.read_bytes() == b"updated remote PDF"
    assert bucket_state["download_count"] == 2

    bucket_state["content"] = None
    assert service.list_pdf_files() == []
    assert service.get_storage_status()["status"] == "supabase"
    assert not (tmp_path / "cache" / "remote.pdf").exists()


def test_document_service_falls_back_to_local_folder_when_supabase_fails(
    monkeypatch, settings, tmp_path, caplog
):
    settings.SUPABASE_URL = "https://example.supabase.co"
    settings.SUPABASE_SERVICE_ROLE_KEY = "test-service-key"
    settings.DOCUMENT_STORAGE_PATH = str(tmp_path / "local")
    settings.SUPABASE_CACHE_PATH = str(tmp_path / "cache")
    local_path = tmp_path / "local"
    local_path.mkdir()
    local_pdf = local_path / "local.pdf"
    local_pdf.write_bytes(b"local PDF")

    class FakeBucket:
        def list(self, path=""):
            raise OSError("test storage outage")

    class FakeStorage:
        def from_(self, name):
            return FakeBucket()

    class FakeClient:
        storage = FakeStorage()

    monkeypatch.setattr("app.services.document_service.create_client", lambda url, key: FakeClient())
    service = DocumentService(settings)

    assert service.list_pdf_files() == [local_pdf]
    status = service.get_storage_status()
    assert status["status"] == "fallback"
    assert "local PDF folder" in status["warning"]
    assert "Supabase Storage synchronization failed" in caplog.text


def test_document_service_local_development_corpus_uses_company_pdfs(settings):
    settings.DOCUMENT_STORAGE_MODE = "local"
    settings.DOCUMENT_STORAGE_PATH = "sample-docs"
    settings.DOCUMENT_EXCLUDE_FILES = "Sample_Questions.pdf"
    settings.SUPABASE_URL = "https://example.supabase.co"
    settings.SUPABASE_SERVICE_ROLE_KEY = "test-service-key"

    service = DocumentService(settings)
    _, files = service.load_documents()
    inventory = service.get_document_inventory()

    assert len(files) == 4
    assert all(path.name != "Sample_Questions.pdf" for path in files)
    assert service.get_storage_status()["status"] == "local"
    revisions = {
        item["name"]: item["revision_status"]
        for item in inventory
        if item["name"].startswith("QC-SOP-014")
    }
    assert revisions["QC-SOP-014_Rev03_Incoming_Material_Inspection.pdf"] == "superseded"
    assert revisions["QC-SOP-014_Rev04_Incoming_Material_Inspection.pdf"] == "current"


def test_document_service_saves_uploaded_pdf_to_local_upload_folder(settings, tmp_path):
    settings.DOCUMENT_STORAGE_MODE = "local"
    settings.DOCUMENT_STORAGE_PATH = str(tmp_path / "source")
    settings.DOCUMENT_UPLOAD_PATH = str(tmp_path / "uploads")
    settings.SUPABASE_URL = None
    settings.SUPABASE_SERVICE_ROLE_KEY = None
    service = DocumentService(settings)
    content = b"%PDF-1.7\nmock document"

    saved_name = service.save_uploaded_pdf("../operator-guide.pdf", content)

    assert saved_name == "operator-guide.pdf"
    assert (tmp_path / "uploads" / saved_name).read_bytes() == content
    assert [path.name for path in service.list_pdf_files()] == [saved_name]


def test_document_service_rejects_non_pdf_upload(settings, tmp_path):
    settings.DOCUMENT_STORAGE_MODE = "local"
    settings.DOCUMENT_STORAGE_PATH = str(tmp_path / "source")
    settings.DOCUMENT_UPLOAD_PATH = str(tmp_path / "uploads")
    service = DocumentService(settings)

    with pytest.raises(ValueError, match="not a valid PDF"):
        service.save_uploaded_pdf("report.pdf", b"not a PDF")


def test_queued_upload_does_not_fall_back_to_local_storage(monkeypatch, settings, tmp_path):
    from app.services.document_service import StorageUploadError

    settings.DOCUMENT_STORAGE_MODE = "supabase"
    settings.DOCUMENT_STORAGE_PATH = str(tmp_path / "source")
    settings.DOCUMENT_UPLOAD_PATH = str(tmp_path / "uploads")
    settings.SUPABASE_URL = "https://storage.example.test"
    settings.SUPABASE_SERVICE_ROLE_KEY = "test-service-key"

    class BrokenStorageBucket:
        def upload(self, *args, **kwargs):
            raise RuntimeError("storage unavailable")

    class FakeStorage:
        def from_(self, bucket_name):
            return BrokenStorageBucket()

    class FakeClient:
        storage = FakeStorage()

    monkeypatch.setattr("app.services.document_service.create_client", lambda *args: FakeClient())
    service = DocumentService(settings)

    with pytest.raises(StorageUploadError, match="queued ingestion requires shared Supabase"):
        service.save_uploaded_pdf(
            "operator-guide.pdf",
            b"%PDF-1.7\nmock document",
            allow_local_fallback=False,
        )

    assert not (tmp_path / "uploads" / "operator-guide.pdf").exists()


def test_ingestion_queue_is_selected_only_for_shared_qdrant(settings):
    from app.services.ingestion_queue import get_ingestion_mode

    settings.INGESTION_MODE = "auto"
    settings.REDIS_URL = "rediss://redis.example.test:6379"
    settings.QDRANT_MODE = "cloud"
    settings.QDRANT_URL = "https://qdrant.example.test"
    settings.SUPABASE_URL = "https://storage.example.test"
    settings.SUPABASE_SERVICE_ROLE_KEY = "test-service-key"
    assert get_ingestion_mode(settings) == "queue"

    settings.QDRANT_MODE = "local"
    assert get_ingestion_mode(settings) == "sync"

    settings.INGESTION_MODE = "queue"
    settings.QDRANT_MODE = "cloud"
    settings.SUPABASE_URL = None
    with pytest.raises(ValueError, match="shared Supabase document storage"):
        from app.services.ingestion_queue import create_ingestion_queue

        create_ingestion_queue(settings)


def test_upload_endpoint_stores_and_indexes_pdf_synchronously(monkeypatch, settings, tmp_path):
    import pymupdf
    from fastapi.testclient import TestClient

    import app.main as main

    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Operator checks the machine guard before startup.")
    content = pdf.tobytes()
    pdf.close()

    settings.INGESTION_MODE = "sync"
    settings.DOCUMENT_STORAGE_MODE = "local"
    settings.DOCUMENT_STORAGE_PATH = str(tmp_path / "source")
    settings.DOCUMENT_UPLOAD_PATH = str(tmp_path / "uploads")
    settings.SUPABASE_URL = None
    settings.SUPABASE_SERVICE_ROLE_KEY = None
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "document_service", DocumentService(settings))
    monkeypatch.setattr(main, "embedding_service", object())

    class FakeVectorStore:
        def upsert_documents(self, documents, reset=False):
            return {"indexed": len(documents)}

    monkeypatch.setattr(main, "vector_store", FakeVectorStore())
    response = TestClient(main.app).post(
        "/api/documents/upload",
        files={"file": ("operator-guide.pdf", content, "application/pdf")},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "completed"
    assert result["chunks_indexed"] > 0
    assert result["document"] == "operator-guide.pdf"
    assert (tmp_path / "uploads" / "operator-guide.pdf").is_file()


def test_qdrant_cloud_uses_configured_url_and_api_key(monkeypatch, settings):
    settings.QDRANT_URL = "https://qdrant.example.test"
    settings.QDRANT_API_KEY = "test-qdrant-key"
    client_options = {}

    class FakeCloudQdrantClient(FakeQdrantClient):
        def __init__(self, **kwargs):
            client_options.update(kwargs)
            super().__init__()

    monkeypatch.setattr("app.services.vector_store.QdrantClient", FakeCloudQdrantClient)
    QdrantVectorStore(settings, FakeEmbeddingService())

    assert client_options == {
        "url": "https://qdrant.example.test",
        "api_key": "test-qdrant-key",
    }


def test_qdrant_local_mode_ignores_cloud_url(monkeypatch, settings, tmp_path):
    settings.QDRANT_MODE = "local"
    settings.QDRANT_URL = "https://qdrant.example.test"
    settings.QDRANT_API_KEY = "test-qdrant-key"
    settings.QDRANT_STORAGE_PATH = str(tmp_path / "local-qdrant")
    client_options = {}

    class FakeLocalQdrantClient(FakeQdrantClient):
        def __init__(self, *args, **kwargs):
            client_options.update(kwargs)
            super().__init__()

    monkeypatch.setattr("app.services.vector_store.QdrantClient", FakeLocalQdrantClient)
    QdrantVectorStore(settings, FakeEmbeddingService())

    assert client_options == {
        "path": str(tmp_path / "local-qdrant"),
    }


def test_ingestion_service_assigns_metadata(settings):
    indexed_metadata = []

    class FakeDocumentService:
        def load_documents(self, document_names=None):
            return (
                [
                    Document(
                        page_content="First page of procedure text.\n\nSecond paragraph for split.",
                        metadata={
                            "source_file": "sample.pdf",
                            "page": 2,
                            "document_hash": "doc-123",
                            "document_family": "SAMPLE",
                            "revision": None,
                            "effective_date": None,
                            "revision_status": "current",
                            "superseded_by": None,
                        },
                    )
                ],
                ["sample.pdf"],
            )

        def get_document_inventory(self):
            return [{
                "name": "sample.pdf",
                "processing_status": "ready",
                "processing_error": None,
            }]

    class FakeVectorStore:
        def upsert_documents(self, documents, reset=False):
            assert documents[0].metadata["chunk_id"]
            indexed_metadata.extend(document.metadata for document in documents)
            return {"indexed": len(documents)}

    ingestion = IngestionService(settings, FakeDocumentService(), FakeVectorStore())
    result = ingestion.ingest(force_reindex=False)
    assert result["pdf_count"] == 1
    assert result["chunks_indexed"] >= 1
    assert indexed_metadata[0]["revision_status"] == "current"


def test_revision_inspection_reads_cover_text_and_filename():
    revision = inspect_revision(
        "SOP-101-Rev-02.pdf",
        "Document: Line startup\nRevision: 3\nEffective Date: 2025-02-14",
    )

    assert revision == {
        "document_family": "SOP101",
        "revision": "3",
        "effective_date": "2025-02-14",
    }


def test_revision_classification_marks_latest_and_future_documents():
    documents = classify_revisions(
        [
            {
                "name": "SOP-101-Rev-1.pdf",
                "document_family": "SOP101",
                "revision": "1",
                "effective_date": "2024-01-01",
            },
            {
                "name": "SOP-101-Rev-2.pdf",
                "document_family": "SOP101",
                "revision": "2",
                "effective_date": "2025-01-01",
            },
            {
                "name": "SOP-101-Rev-3.pdf",
                "document_family": "SOP101",
                "revision": "3",
                "effective_date": "2027-01-01",
            },
        ],
        effective_today=date(2026, 1, 1),
    )

    assert documents[0]["revision_status"] == "superseded"
    assert documents[0]["superseded_by"] == "SOP-101-Rev-2.pdf"
    assert documents[1]["revision_status"] == "current"
    assert documents[2]["revision_status"] == "not_yet_effective"


def test_assignment_revision_names_group_into_one_document_family():
    older = inspect_revision(
        "QC-SOP-014 Rev03 - Incoming Material Inspection.pdf",
        "Revision: Rev03 - March 15, 2023",
    )
    newer = inspect_revision(
        "QC-SOP-014 Rev04 - Incoming Material Inspection.pdf",
        "Revision: Rev04 - July 1, 2026",
    )

    assert older["document_family"] == newer["document_family"]
    assert older["revision"] == "03"
    assert newer["revision"] == "04"
    assert older["effective_date"] == "2023-03-15"
    assert newer["effective_date"] == "2026-07-01"
    classified = classify_revisions(
        [
            {"name": "Rev03.pdf", **older},
            {"name": "Rev04.pdf", **newer},
        ],
        effective_today=date(2026, 11, 10),
    )
    assert classified[0]["revision_status"] == "superseded"
    assert classified[1]["revision_status"] == "current"


def test_revision_status_stays_unknown_when_no_revision_evidence_exists():
    document = classify_revisions(
        [{"name": "unversioned.pdf", "document_family": "UNVERSIONED", "revision": None, "effective_date": None}]
    )[0]
    assert document["revision_status"] == "revision_unknown"


def test_rag_prefers_current_revision_over_higher_scoring_superseded_hit(settings):
    settings.TOP_K = 1

    class RevisionVectorStore:
        def search(self, query_vector, limit=4):
            assert limit == 5
            return [
                {
                    "score": 0.99,
                    "payload": {
                        "document": "SOP-101-Rev-1.pdf",
                        "page": 1,
                        "text": "Old startup procedure.",
                        "revision": "1",
                        "revision_status": "superseded",
                        "superseded_by": "SOP-101-Rev-2.pdf",
                    },
                },
                {
                    "score": 0.75,
                    "payload": {
                        "document": "SOP-101-Rev-2.pdf",
                        "page": 1,
                        "text": "Current startup procedure.",
                        "revision": "2",
                        "revision_status": "current",
                    },
                },
            ]

    class FakeLanguageModel:
        def invoke(self, messages):
            return SimpleNamespace(content="Use the current startup procedure [SOP-101-Rev-2.pdf, page 1].")

    rag = RAGService(settings, FakeEmbeddingService(), RevisionVectorStore())
    rag.llm = FakeLanguageModel()
    result = rag.answer_question("How do I start the line?")

    assert result["sources"][0]["document"] == "SOP-101-Rev-2.pdf"
    assert result["sources"][0]["revision_status"] == "current"
    assert result["sources"][1]["revision_status"] == "superseded"
    assert result["sources"][1]["superseded_by"] == "SOP-101-Rev-2.pdf"


def test_rag_rejects_answer_with_citation_not_in_retrieved_sources(settings):
    class OneHitVectorStore:
        def search(self, query_vector, limit=4):
            return [{
                "score": 0.8,
                "payload": {
                    "document": "known.pdf",
                    "page": 2,
                    "text": "Known procedure.",
                    "revision_status": "current",
                },
            }]

    class UnverifiedLanguageModel:
        def invoke(self, messages):
            return SimpleNamespace(content="The answer is 42 [other.pdf, page 8].")

    rag = RAGService(settings, FakeEmbeddingService(), OneHitVectorStore())
    rag.llm = UnverifiedLanguageModel()
    result = rag.answer_question("What does the document say?")

    assert result["answer"] == UNKNOWN_ANSWER
    assert result["sources"] == []
    assert result["found_in_documents"] is False


def test_rag_never_uses_not_yet_effective_revisions(settings):
    class FutureDocumentStore:
        def search(self, query_vector, limit=4):
            return [{
                "score": 0.95,
                "payload": {
                    "document": "future.pdf",
                    "page": 1,
                    "text": "Future procedure.",
                    "revision_status": "not_yet_effective",
                },
            }]

    rag = RAGService(settings, FakeEmbeddingService(), FutureDocumentStore())
    result = rag.answer_question("What is the future procedure?")

    assert result["answer"] == UNKNOWN_ANSWER
    assert result["sources"] == []
    assert result["found_in_documents"] is False


def test_rag_returns_unknown_answer_when_search_is_empty(settings):
    class FakeVectorStore:
        def search(self, query_vector, limit=4):
            return []

    rag = RAGService(settings, FakeEmbeddingService(), FakeVectorStore())
    result = rag.answer_question("What is the emergency shutdown procedure?")
    assert result["answer"] == UNKNOWN_ANSWER
    assert result["found_in_documents"] is False


def test_question_schema_rejects_blank_question():
    from app.models import QuestionRequest

    with pytest.raises(ValueError):
        QuestionRequest(question=" ")


def test_question_schema_rejects_unbounded_input():
    from app.models import QuestionRequest

    with pytest.raises(ValueError):
        QuestionRequest(question="x" * 2001)
