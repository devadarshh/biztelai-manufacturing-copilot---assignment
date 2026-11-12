from __future__ import annotations

import logging

from rq import Worker
from rq.serializers import JSONSerializer

from app.config import Settings
from app.services.document_service import DocumentService
from app.services.embedding_service import EmbeddingService
from app.services.ingestion_queue import create_ingestion_queue, get_ingestion_mode
from app.services.ingestion_service import IngestionService
from app.services.vector_store import QdrantVectorStore

logger = logging.getLogger(__name__)


def process_uploaded_document(document_name: str) -> dict:
    settings = Settings()
    document_service = DocumentService(settings)
    if not any(path.name == document_name for path in document_service.list_pdf_files()):
        raise FileNotFoundError(f"Uploaded PDF {document_name!r} is not available in the configured storage.")

    embedding_service = EmbeddingService(settings)
    vector_store = QdrantVectorStore(settings, embedding_service)
    result = IngestionService(settings, document_service, vector_store).ingest(
        document_names={document_name},
    )
    if not result["chunks_indexed"]:
        raise ValueError(f"No text chunks were indexed for {document_name!r}.")
    logger.info(
        "Indexed uploaded PDF %s (%s chunks)",
        document_name,
        result["chunks_indexed"],
    )
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    if get_ingestion_mode(settings) != "queue":
        raise SystemExit(
            "The ingestion worker requires INGESTION_MODE=queue, REDIS_URL, and shared Qdrant Cloud configuration."
        )
    queue = create_ingestion_queue(settings)
    logger.info("Listening for document ingestion jobs on queue %s", queue.name)
    Worker(
        [queue],
        connection=queue.connection,
        serializer=JSONSerializer,
    ).work()


if __name__ == "__main__":
    main()
