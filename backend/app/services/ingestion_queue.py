from __future__ import annotations

from redis import Redis
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job
from rq.serializers import JSONSerializer


def is_shared_qdrant_configured(settings) -> bool:
    return settings.QDRANT_MODE == "cloud" or (
        settings.QDRANT_MODE == "auto" and bool(settings.QDRANT_URL)
    )


def is_shared_document_storage_configured(settings) -> bool:
    return bool(settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY)


def get_ingestion_mode(settings) -> str:
    if settings.INGESTION_MODE != "auto":
        return settings.INGESTION_MODE
    if (
        settings.REDIS_URL
        and is_shared_qdrant_configured(settings)
        and is_shared_document_storage_configured(settings)
    ):
        return "queue"
    return "sync"


def create_ingestion_queue(settings) -> Queue:
    if not settings.REDIS_URL:
        raise ValueError("REDIS_URL is required for queued document ingestion.")
    if not is_shared_qdrant_configured(settings):
        raise ValueError(
            "Queued ingestion requires shared Qdrant storage. Configure QDRANT_MODE=cloud and QDRANT_URL."
        )
    if not is_shared_document_storage_configured(settings):
        raise ValueError(
            "Queued ingestion requires shared Supabase document storage. Configure SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY."
        )

    connection = Redis.from_url(settings.REDIS_URL)
    connection.ping()
    return Queue(
        settings.INGESTION_QUEUE_NAME,
        connection=connection,
        default_timeout=settings.INGESTION_JOB_TIMEOUT_SECONDS,
        serializer=JSONSerializer,
    )


def enqueue_document(settings, document_name: str) -> str:
    from app.worker import process_uploaded_document

    queue = create_ingestion_queue(settings)
    job = queue.enqueue(
        process_uploaded_document,
        document_name,
        job_timeout=settings.INGESTION_JOB_TIMEOUT_SECONDS,
        result_ttl=86400,
        failure_ttl=86400,
    )
    return job.id


def get_document_job(settings, job_id: str) -> Job | None:
    queue = create_ingestion_queue(settings)
    try:
        return Job.fetch(job_id, connection=queue.connection, serializer=JSONSerializer)
    except NoSuchJobError:
        return None
