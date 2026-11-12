from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path, PurePosixPath
from threading import RLock
from uuid import uuid4

from langchain_community.document_loaders import PyMuPDFLoader
from langchain_core.documents import Document
from supabase import create_client

from .document_revision import classify_revisions, inspect_revision

logger = logging.getLogger(__name__)


class StorageUploadError(RuntimeError):
    pass


class DocumentService:
    def __init__(self, settings):
        self.settings = settings
        self.storage_path = Path(settings.resolved_documents_path)
        self.cache_path = Path(settings.resolved_supabase_cache_path)
        self.upload_path = Path(settings.resolved_document_upload_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.cache_path.mkdir(parents=True, exist_ok=True)
        self.upload_path.mkdir(parents=True, exist_ok=True)
        self._inventory_signature: tuple[tuple[str, int, int], ...] | None = None
        self._inventory_cache: list[dict] = []
        self._storage_lock = RLock()
        self._storage_initialized = False
        self._local_upload_fallback = False
        self._excluded_files = {
            name.strip().casefold()
            for name in settings.DOCUMENT_EXCLUDE_FILES.split(",")
            if name.strip()
        }
        self._storage_status = {
            "source": "local",
            "status": "local",
            "bucket": settings.SUPABASE_STORAGE_BUCKET,
            "warning": None,
        }

    def _set_local_fallback(self, warning: str) -> Path:
        self._storage_status = {
            "source": "local",
            "status": "fallback",
            "bucket": self.settings.SUPABASE_STORAGE_BUCKET,
            "warning": warning,
        }
        return self.storage_path

    def _sync_supabase_cache(self) -> None:
        client = create_client(
            self.settings.SUPABASE_URL,
            self.settings.SUPABASE_SERVICE_ROLE_KEY,
        )
        bucket = client.storage.from_(self.settings.SUPABASE_STORAGE_BUCKET)
        objects = bucket.list(path="")
        remote_files: dict[str, dict] = {}
        for item in objects:
            name = item.get("name")
            if not isinstance(name, str) or PurePosixPath(name).name != name or "\\" in name:
                continue
            if not self._is_document_pdf(name):
                continue
            remote_files[name] = item

        manifest_path = self.cache_path / ".supabase_manifest.json"
        manifest = {}
        if manifest_path.exists():
            with manifest_path.open("r", encoding="utf-8") as manifest_file:
                manifest = json.load(manifest_file)
            if not isinstance(manifest, dict):
                raise ValueError("Supabase PDF cache manifest is invalid.")

        next_manifest: dict[str, dict[str, str | int | None]] = {}
        for name, metadata in remote_files.items():
            cached_path = self.cache_path / name
            remote_updated_at = metadata.get("updated_at")
            file_metadata = metadata.get("metadata")
            remote_size = file_metadata.get("size") if isinstance(file_metadata, dict) else None
            if isinstance(remote_size, str) and remote_size.isdigit():
                remote_size = int(remote_size)
            previous = manifest.get(name, {})
            unchanged = (
                cached_path.is_file()
                and previous.get("updated_at") == remote_updated_at
                and previous.get("size") == remote_size
                and (remote_updated_at is not None or remote_size is not None)
            )
            if not unchanged:
                content = bucket.download(name)
                if not isinstance(content, bytes):
                    raise TypeError(f"Supabase returned invalid PDF data for {name}.")
                temporary_path = self.cache_path / f".{uuid4().hex}.tmp"
                try:
                    with temporary_path.open("wb") as downloaded_file:
                        downloaded_file.write(content)
                        downloaded_file.flush()
                        os.fsync(downloaded_file.fileno())
                    os.replace(temporary_path, cached_path)
                finally:
                    temporary_path.unlink(missing_ok=True)
                if remote_size is not None and cached_path.stat().st_size != remote_size:
                    raise ValueError(f"Downloaded PDF size did not match Supabase metadata for {name}.")

            next_manifest[name] = {
                "updated_at": remote_updated_at,
                "size": remote_size,
            }

        temporary_manifest = self.cache_path / f".{uuid4().hex}.manifest.tmp"
        try:
            with temporary_manifest.open("w", encoding="utf-8") as manifest_file:
                json.dump(next_manifest, manifest_file)
                manifest_file.flush()
                os.fsync(manifest_file.fileno())
            os.replace(temporary_manifest, manifest_path)
        finally:
            temporary_manifest.unlink(missing_ok=True)

        for cached_path in self.cache_path.iterdir():
            if (
                cached_path.is_file()
                and cached_path.suffix.casefold() == ".pdf"
                and cached_path.name not in remote_files
            ):
                cached_path.unlink()

        self._storage_status = {
            "source": "supabase",
            "status": "supabase",
            "bucket": self.settings.SUPABASE_STORAGE_BUCKET,
            "warning": None,
        }

    def list_pdf_files(self) -> list[Path]:
        with self._storage_lock:
            has_supabase_setting = bool(self.settings.SUPABASE_URL or self.settings.SUPABASE_SERVICE_ROLE_KEY)
            if self.settings.DOCUMENT_STORAGE_MODE == "local" or (
                self.settings.DOCUMENT_STORAGE_MODE == "auto" and not has_supabase_setting
            ):
                active_path = self.storage_path
                self._storage_status = {
                    "source": "local",
                    "status": "local",
                    "bucket": self.settings.SUPABASE_STORAGE_BUCKET,
                    "warning": None,
                }
            elif not has_supabase_setting:
                warning = "Supabase Storage is not configured; using the local PDF folder."
                logger.warning(warning)
                active_path = self._set_local_fallback(warning)
            elif not self.settings.SUPABASE_URL or not self.settings.SUPABASE_SERVICE_ROLE_KEY:
                warning = "Supabase Storage is not fully configured; using the local PDF folder."
                logger.warning(warning)
                active_path = self._set_local_fallback(warning)
            else:
                try:
                    self._sync_supabase_cache()
                    active_path = self.cache_path
                except Exception as exc:
                    warning = "Could not sync PDFs from Supabase Storage; using the local PDF folder."
                    logger.error(
                        "Supabase Storage synchronization failed for bucket %s (%s)",
                        self.settings.SUPABASE_STORAGE_BUCKET,
                        type(exc).__name__,
                    )
                    active_path = self._set_local_fallback(warning)

            self._storage_initialized = True
            files_by_name = {
                path.name.casefold(): path
                for path in active_path.iterdir()
                if path.is_file() and self._is_document_pdf(path.name)
            }
            files_by_name.update(
                {
                    path.name.casefold(): path
                    for path in self.upload_path.iterdir()
                    if path.is_file() and self._is_document_pdf(path.name)
                }
            )
            if self._local_upload_fallback and self._storage_status["status"] == "supabase":
                self._storage_status = {
                    "source": "mixed",
                    "status": "fallback",
                    "bucket": self.settings.SUPABASE_STORAGE_BUCKET,
                    "warning": "Some uploaded PDFs are saved locally because Supabase upload failed.",
                }
            return sorted(files_by_name.values(), key=lambda path: path.name.casefold())

    def _is_document_pdf(self, name: str) -> bool:
        return (
            Path(name).suffix.casefold() == ".pdf"
            and name.casefold() not in self._excluded_files
        )

    def save_uploaded_pdf(
        self,
        filename: str,
        content: bytes,
        *,
        allow_local_fallback: bool = True,
    ) -> str:
        safe_name = Path(filename).name
        safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", safe_name).strip(" .")
        if not safe_name or not safe_name.casefold().endswith(".pdf"):
            raise ValueError("Upload must have a valid PDF filename.")
        if not content.startswith(b"%PDF-"):
            raise ValueError("The uploaded file is not a valid PDF.")

        supabase_configured = bool(
            self.settings.SUPABASE_URL and self.settings.SUPABASE_SERVICE_ROLE_KEY
        )
        use_supabase = self.settings.DOCUMENT_STORAGE_MODE == "supabase" or (
            self.settings.DOCUMENT_STORAGE_MODE == "auto" and supabase_configured
        )
        if use_supabase and not supabase_configured:
            raise ValueError(
                "Supabase upload mode requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY."
            )

        if use_supabase:
            try:
                client = create_client(
                    self.settings.SUPABASE_URL,
                    self.settings.SUPABASE_SERVICE_ROLE_KEY,
                )
                client.storage.from_(self.settings.SUPABASE_STORAGE_BUCKET).upload(
                    safe_name,
                    content,
                    file_options={
                        "content-type": "application/pdf",
                        "upsert": "true",
                    },
                )
                self._storage_status = {
                    "source": "supabase",
                    "status": "supabase",
                    "bucket": self.settings.SUPABASE_STORAGE_BUCKET,
                    "warning": None,
                }
                self._local_upload_fallback = False
            except Exception as exc:
                if not allow_local_fallback:
                    logger.error(
                        "Supabase upload failed for queued PDF %s (%s)",
                        safe_name,
                        type(exc).__name__,
                    )
                    raise StorageUploadError(
                        "Supabase upload failed; queued ingestion requires shared Supabase Storage."
                    ) from exc
                warning = "Could not upload to Supabase Storage; the PDF was saved locally instead."
                logger.error(
                    "Supabase upload failed for PDF %s (%s)",
                    safe_name,
                    type(exc).__name__,
                )
                self._set_local_fallback(warning)
                self._write_local_upload(safe_name, content)
                self._local_upload_fallback = True
        else:
            self._write_local_upload(safe_name, content)
            self._storage_status = {
                "source": "local",
                "status": "local",
                "bucket": self.settings.SUPABASE_STORAGE_BUCKET,
                "warning": None,
            }

        with self._storage_lock:
            self._storage_initialized = False
            self._inventory_signature = None
            self._inventory_cache = []
        return safe_name

    def _write_local_upload(self, filename: str, content: bytes) -> None:
        destination = self.upload_path / filename
        temporary_path = self.upload_path / f".{uuid4().hex}.tmp"
        try:
            with temporary_path.open("wb") as uploaded_file:
                uploaded_file.write(content)
                uploaded_file.flush()
                os.fsync(uploaded_file.fileno())
            os.replace(temporary_path, destination)
        finally:
            temporary_path.unlink(missing_ok=True)

    def get_storage_status(self) -> dict:
        with self._storage_lock:
            if not self._storage_initialized:
                self.list_pdf_files()
            return dict(self._storage_status)

    def _document_hash(self, file_path: Path) -> str:
        digest = hashlib.sha256()
        with file_path.open("rb") as file_handle:
            for chunk in iter(lambda: file_handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def load_documents(
        self,
        document_names: set[str] | None = None,
    ) -> tuple[list[Document], list[Path]]:
        pdf_files = self.list_pdf_files()
        if document_names is not None:
            requested_names = {name.casefold() for name in document_names}
            pdf_files = [path for path in pdf_files if path.name.casefold() in requested_names]
        extracted_documents: list[Document] = []
        pages_by_file: dict[str, list[Document]] = {}
        errors_by_file: dict[str, str] = {}

        for file_path in pdf_files:
            try:
                loader = PyMuPDFLoader(str(file_path))
                loaded_pages = loader.load()
            except Exception:
                logger.exception("Unable to extract text from PDF %s", file_path.name)
                errors_by_file[file_path.name] = "PDF text extraction failed."
                continue

            document_hash = self._document_hash(file_path)
            pages_by_file[file_path.name] = loaded_pages
            for page in loaded_pages:
                metadata = dict(page.metadata or {})
                metadata["source_file"] = file_path.name
                metadata["document_name"] = file_path.name
                metadata["document_hash"] = document_hash
                metadata["file_path"] = str(file_path)
                raw_page = metadata.get("page")
                metadata["page"] = int(raw_page) + 1 if raw_page is not None else 1
                metadata["section"] = metadata.get("section")
                page.metadata = metadata
                extracted_documents.append(page)

        revision_inventory = []
        for file_path in pdf_files:
            pages = pages_by_file.get(file_path.name, [])
            revision = inspect_revision(
                file_path.name,
                "\n".join(page.page_content for page in pages[:3]),
            )
            revision_inventory.append(
                {
                    "name": file_path.name,
                    "size_bytes": file_path.stat().st_size,
                    "processing_status": (
                        "error" if file_path.name in errors_by_file
                        else "ready" if any(page.page_content.strip() for page in pages_by_file.get(file_path.name, []))
                        else "no_extractable_text"
                    ),
                    "processing_error": errors_by_file.get(file_path.name),
                    **revision,
                }
            )

        revision_inventory = classify_revisions(revision_inventory)
        revisions_by_name = {item["name"]: item for item in revision_inventory}
        for document in extracted_documents:
            document.metadata.update(
                {
                    key: revisions_by_name[document.metadata["source_file"]][key]
                    for key in ("document_family", "revision", "effective_date", "revision_status", "superseded_by")
                }
            )

        self._inventory_signature = tuple(
            (file_path.name, file_path.stat().st_size, file_path.stat().st_mtime_ns)
            for file_path in pdf_files
        )
        self._inventory_cache = revision_inventory
        return extracted_documents, pdf_files

    def get_document_inventory(self) -> list[dict]:
        pdf_files = self.list_pdf_files()
        signature = tuple(
            (file_path.name, file_path.stat().st_size, file_path.stat().st_mtime_ns)
            for file_path in pdf_files
        )
        if signature == self._inventory_signature:
            return [dict(document) for document in self._inventory_cache]

        inventory = []

        for file_path in pdf_files:
            try:
                pages = PyMuPDFLoader(str(file_path)).load()
            except Exception:
                logger.exception("Unable to inspect PDF %s", file_path.name)
                pages = []
            inventory.append(
                {
                    "name": file_path.name,
                    "size_bytes": file_path.stat().st_size,
                    "processing_status": (
                        "ready" if any(page.page_content.strip() for page in pages)
                        else "no_extractable_text" if pages
                        else "error"
                    ),
                    "processing_error": None if pages else "PDF text extraction failed.",
                    **inspect_revision(
                        file_path.name,
                        "\n".join(page.page_content for page in pages[:3]),
                    ),
                }
            )

        self._inventory_cache = classify_revisions(inventory)
        self._inventory_signature = signature
        return [dict(document) for document in self._inventory_cache]
