from __future__ import annotations

import logging
import re

import httpx
from google.api_core.exceptions import GoogleAPIError, Unauthorized
from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from huggingface_hub.errors import HfHubHTTPError

from app.config import settings
from app.models import AskResponse, HealthResponse, IngestRequest, IngestResponse, QuestionRequest, SourceEvidence, UploadResponse
from app.services.document_service import DocumentService, StorageUploadError
from app.services.embedding_service import EmbeddingService
from app.services.ingestion_queue import create_ingestion_queue, enqueue_document, get_document_job, get_ingestion_mode
from app.services.ingestion_service import IngestionService
from app.services.qdrant_vector_store import QdrantVectorStore
from app.services.rag_service import RAGService

logger = logging.getLogger(__name__)
app = FastAPI(title="BiztelAI Manufacturing Copilot", version="1.0.0")


def gemini_provider_error(exc: GoogleAPIError) -> HTTPException:
    if isinstance(exc, Unauthorized) or getattr(exc, "code", None) == 401:
        detail = (
            "Google Gemini rejected GEMINI_API_KEY. Create or rotate a Gemini API key in Google AI Studio, "
            "update GEMINI_API_KEY in the root .env, and restart the backend."
        )
    else:
        detail = "Google Gemini could not complete the request. Check the API key, model access, quota, and backend logs."
    logger.warning("Gemini API request failed (%s)", type(exc).__name__)
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


def hosted_embedding_error(exc: HfHubHTTPError) -> HTTPException:
    response = getattr(exc, "response", None)
    provider_status = getattr(response, "status_code", None)
    if provider_status == 402:
        detail = (
            "Hugging Face hosted inference returned 402 Payment Required. "
            "A valid API key does not include inference credits. Add Hugging Face inference credits, "
            "or use HF_EMBEDDING_PROVIDER=local and restart the backend."
        )
    elif provider_status in {401, 403}:
        detail = (
            "Hugging Face rejected the hosted inference request. Verify the token and its permissions, "
            "or use HF_EMBEDDING_PROVIDER=local."
        )
    else:
        detail = (
            "Hugging Face hosted inference failed. Check provider availability and configuration, "
            "or use HF_EMBEDDING_PROVIDER=local."
        )
    logger.warning("Hosted embedding provider returned HTTP %s", provider_status)
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


origins = ["*"] if settings.CORS_ORIGINS == "*" else [origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

document_service = DocumentService(settings)
embedding_service = None
vector_store = None
rag_service = None

try:
    embedding_service = EmbeddingService(settings)
    vector_store = QdrantVectorStore(settings, embedding_service)
    rag_service = RAGService(settings, embedding_service, vector_store)
except ValueError as exc:  # pragma: no cover - runtime configuration guard
    logger.error("Application configuration is incomplete: %s", exc)
    app.state.configuration_error = str(exc)
except Exception:  # pragma: no cover - runtime configuration guard
    logger.exception("AI services failed during application startup")
    app.state.configuration_error = "AI services failed during startup. Check backend logs."


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    embeddings_configured = bool(embedding_service)
    llm_configured = bool(rag_service)
    qdrant_ready = bool(vector_store)
    return HealthResponse(
        status="ok" if embeddings_configured and llm_configured and qdrant_ready else "degraded",
        backend="fastapi",
        embeddings_configured=embeddings_configured,
        llm_configured=llm_configured,
        qdrant_ready=qdrant_ready,
    )


@app.get("/api/documents")
def list_documents() -> list[dict]:
    return document_service.get_document_inventory()


@app.get("/api/storage")
def storage_status() -> dict:
    return document_service.get_storage_status()


@app.post("/api/ingest", response_model=IngestResponse)
def ingest_documents(payload: IngestRequest = IngestRequest()) -> IngestResponse:
    if not embedding_service or not vector_store:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The embedding service is unavailable. Check HF_EMBEDDING_PROVIDER and embedding model configuration.",
        )

    try:
        ingestion_service = IngestionService(settings, document_service, vector_store)
        result = ingestion_service.ingest(force_reindex=payload.reindex)
    except HfHubHTTPError as exc:
        raise hosted_embedding_error(exc) from None
    except Exception:
        logger.exception("Document ingestion failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document ingestion failed. Check backend logs for details.",
        ) from None
    return IngestResponse(**result)


@app.post("/api/documents/upload", response_model=UploadResponse)
async def upload_document(file: UploadFile = File(...)) -> UploadResponse:
    if not embedding_service or not vector_store:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The embedding service is unavailable. Check HF_EMBEDDING_PROVIDER and embedding model configuration.",
        )
    if not file.filename or not file.filename.casefold().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files can be uploaded.",
        )

    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"PDF exceeds the {settings.MAX_UPLOAD_SIZE_MB} MB upload limit.",
        )

    ingestion_mode = get_ingestion_mode(settings)
    if ingestion_mode == "queue":
        try:
            create_ingestion_queue(settings)
        except Exception:
            logger.exception("Document ingestion queue is unavailable")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The document processing queue is unavailable. Check Redis and worker configuration.",
            ) from None

    try:
        document_name = document_service.save_uploaded_pdf(
            file.filename,
            content,
            allow_local_fallback=ingestion_mode != "queue",
        )
        if ingestion_mode == "queue":
            job_id = enqueue_document(settings, document_name)
            return UploadResponse(
                status="queued",
                message="PDF uploaded. It will become searchable after the worker finishes indexing it.",
                document=document_name,
                job_id=job_id,
            )

        result = IngestionService(settings, document_service, vector_store).ingest(
            document_names={document_name},
        )
        if not result["chunks_indexed"]:
            raise ValueError("The PDF contained no indexable text.")
        return UploadResponse(
            status="completed",
            message="PDF uploaded and indexed successfully.",
            document=document_name,
            chunks_indexed=result["chunks_indexed"],
        )
    except HfHubHTTPError as exc:
        raise hosted_embedding_error(exc) from None
    except StorageUploadError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Document upload or indexing failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document upload or indexing failed. Check backend logs.",
        ) from None
    finally:
        await file.close()


@app.get("/api/jobs/{job_id}")
def get_ingestion_job_status(job_id: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid job ID.",
        )
    if get_ingestion_mode(settings) != "queue":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job status is available only when queued ingestion is enabled.",
        )
    try:
        job = get_document_job(settings, job_id)
    except Exception:
        logger.exception("Unable to read document ingestion job status")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The document processing queue is unavailable.",
        ) from None
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document ingestion job was not found or has expired.",
        )

    job_status = job.get_status(refresh=True).value
    response = {
        "job_id": job.id,
        "status": job_status,
        "document": job.args[0] if job.args else None,
    }
    if job_status == "finished":
        response["result"] = job.result
    elif job_status == "failed":
        response["message"] = "Document indexing failed. Check the worker logs."
    return response


@app.post("/api/ask", response_model=AskResponse)
def ask_question(payload: QuestionRequest) -> AskResponse:
    if not rag_service or not vector_store or not embedding_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Required AI services are unavailable. Check GEMINI_API_KEY and the embedding provider configuration.",
        )

    try:
        result = rag_service.answer_question(payload.question)
    except HfHubHTTPError as exc:
        raise hosted_embedding_error(exc) from None
    except GoogleAPIError as exc:
        raise gemini_provider_error(exc) from None
    except httpx.HTTPStatusError as exc:
        auth_error = mistral_authentication_error(exc)
        if auth_error is not None:
            raise auth_error from None
        logger.exception("AI provider returned an unsuccessful HTTP response")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="An AI provider returned an unsuccessful response. Check backend logs and provider status.",
        ) from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception:
        logger.exception("Failed to answer a question")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The assistant could not complete the request. Check backend logs and service configuration.",
        ) from None

    sources = [
        SourceEvidence(
            document=item["document"],
            page=item.get("page"),
            excerpt=item.get("excerpt") or "",
            score=item.get("score"),
            section=item.get("section"),
            revision=item.get("revision"),
            effective_date=item.get("effective_date"),
            revision_status=item.get("revision_status"),
            superseded_by=item.get("superseded_by"),
        )
        for item in result.get("sources", [])
    ]
    return AskResponse(
        answer=result["answer"],
        sources=sources,
        found_in_documents=result.get("found_in_documents", False),
        confidence=result.get("confidence", "low"),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.APP_HOST, port=settings.APP_PORT, reload=False)
