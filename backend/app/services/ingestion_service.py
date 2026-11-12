from __future__ import annotations

import hashlib
import uuid

from langchain_text_splitters import RecursiveCharacterTextSplitter

from .document_service import DocumentService


class IngestionService:
    def __init__(self, settings, document_service: DocumentService, vector_store):
        self.settings = settings
        self.document_service = document_service
        self.vector_store = vector_store
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.CHUNK_SIZE,
            chunk_overlap=settings.CHUNK_OVERLAP,
            separators=["\n\n", "\n", " ", ""],
            length_function=len,
        )

    def _chunk_id(self, document_name: str, page_number: int, chunk_index: int, document_hash: str) -> str:
        key = f"{document_name}:{page_number}:{chunk_index}:{document_hash}"
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return str(uuid.UUID(hex=digest[:32]))

    def ingest(
        self,
        force_reindex: bool = False,
        document_names: set[str] | None = None,
    ) -> dict:
        extracted_documents, pdf_files = self.document_service.load_documents(
            document_names=document_names,
        )
        requested_names = {name.casefold() for name in document_names} if document_names else None
        inventory = [
            item
            for item in self.document_service.get_document_inventory()
            if requested_names is None or item["name"].casefold() in requested_names
        ]
        errors = [
            f"{document['name']}: {document.get('processing_error') or 'PDF has no extractable text.'}"
            for document in inventory
            if document.get("processing_status") not in {"ready"}
        ]
        if not extracted_documents:
            return {
                "status": "success",
                "message": (
                    "No documents were indexed; the existing vector index was left unchanged because no extractable PDF text was available."
                    if pdf_files
                    else "No PDFs were found in the configured storage directory; the existing vector index was left unchanged."
                ),
                "pdf_count": len(pdf_files),
                "chunks_indexed": 0,
                "documents": [],
                "errors": errors,
            }

        split_documents = self.splitter.split_documents(extracted_documents)
        indexed_names = []
        for chunk_index, document in enumerate(split_documents):
            metadata = dict(document.metadata or {})
            if not metadata.get("document_name"):
                metadata["document_name"] = metadata.get("source_file") or "unknown.pdf"
            metadata["page"] = int(metadata.get("page", 1) or 1)
            metadata["chunk_id"] = self._chunk_id(
                metadata["document_name"],
                metadata["page"],
                chunk_index,
                metadata.get("document_hash") or "",
            )
            document.metadata = metadata
            if metadata["document_name"] not in indexed_names:
                indexed_names.append(metadata["document_name"])

        result = self.vector_store.upsert_documents(split_documents, reset=force_reindex)
        return {
            "status": "partial" if errors else "success",
            "message": (
                "PDFs were indexed with processing errors."
                if errors
                else "PDFs were indexed successfully."
            ),
            "pdf_count": len(pdf_files),
            "chunks_indexed": result["indexed"],
            "documents": indexed_names,
            "errors": errors,
        }
