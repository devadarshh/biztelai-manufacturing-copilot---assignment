from __future__ import annotations

import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

UNKNOWN_ANSWER = (
    "I couldn't find this information in the provided manufacturing documents. "
    "Please consult the relevant SOP or an authorized supervisor."
)
DEFAULT_RETRIEVAL_THRESHOLD = 0.35
CITATION_PATTERN = re.compile(r"\[([^\]\n]+),\s*(?:page|p\.?)\s*(\d+)\]", re.IGNORECASE)


def is_abstention(answer: str) -> bool:
    normalized = answer.casefold()
    return any(
        phrase in normalized
        for phrase in (
            "couldn't find this information",
            "could not find this information",
            "couldn't find the information",
            "could not find the information",
            "cannot find the information",
            "cannot be found in the documents",
            "not found in the provided documents",
            "not contained in the provided documents",
            "documents do not contain",
            "documents don't contain",
            "documents do not specify",
            "not available in the documents",
            "not stated in the documents",
            "insufficient information in the provided",
            "not specified in the provided documents",
        )
    )


def has_grounded_citation(answer: str, sources: list[dict]) -> bool:
    cited_pages = {
        (match.group(1).strip().casefold(), int(match.group(2)))
        for match in CITATION_PATTERN.finditer(answer)
    }
    available_pages = {
        (source["document"].casefold(), int(source["page"]))
        for source in sources
        if source.get("document") and source.get("page") is not None
    }
    return bool(cited_pages) and cited_pages.issubset(available_pages)


class RAGService:
    def __init__(self, settings, embedding_service, vector_store):
        self.settings = settings
        self.embedding_service = embedding_service
        self.vector_store = vector_store

        if not settings.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is missing. Add it to your environment before asking questions.")

        self.llm = ChatGoogleGenerativeAI(
            model=settings.GEMINI_MODEL,
            api_key=settings.GEMINI_API_KEY,
            temperature=0.1,
            request_timeout=settings.GEMINI_TIMEOUT_SECONDS,
            retries=2,
        )

    def answer_question(self, question: str) -> dict:
        cleaned_question = (question or "").strip()
        if not cleaned_question:
            raise ValueError("Question cannot be empty.")

        query_vector = self.embedding_service.embed_query(cleaned_question)
        top_k = int(self.settings.TOP_K or 4)
        retrieval_hits = self.vector_store.search(query_vector, limit=top_k * 5)

        if not retrieval_hits:
            return {
                "answer": UNKNOWN_ANSWER,
                "sources": [],
                "found_in_documents": False,
                "confidence": "low",
            }

        retrieval_hits = [
            hit for hit in retrieval_hits
            if (hit.get("payload") or {}).get("revision_status") != "not_yet_effective"
        ]
        if not retrieval_hits:
            return {
                "answer": UNKNOWN_ANSWER,
                "sources": [],
                "found_in_documents": False,
                "confidence": "low",
            }

        revision_priority = {
            "current": 0,
            "revision_unknown": 1,
            "superseded": 3,
        }
        retrieval_hits.sort(
            key=lambda hit: (
                revision_priority.get((hit.get("payload") or {}).get("revision_status"), 1),
                -float(hit.get("score") or 0.0),
            )
        )
        primary_hits = [
            hit for hit in retrieval_hits
            if (hit.get("payload") or {}).get("revision_status") != "superseded"
        ][:top_k]
        superseded_hits = [
            hit for hit in retrieval_hits
            if (hit.get("payload") or {}).get("revision_status") == "superseded"
        ]
        if primary_hits and superseded_hits:
            current_score = float(primary_hits[0].get("score") or 0.0)
            threshold = self.settings.RETRIEVAL_THRESHOLD
            minimum_superseded_score = max(float(threshold or 0.0), current_score * 0.8)
            current_families = {
                (hit.get("payload") or {}).get("document_family")
                for hit in primary_hits
                if (hit.get("payload") or {}).get("document_family")
            }
            supplemental = next(
                (
                    hit for hit in superseded_hits
                    if (hit.get("payload") or {}).get("document_family") in current_families
                    or float(hit.get("score") or 0.0) >= minimum_superseded_score
                ),
                None,
            )
            retrieval_hits = primary_hits + ([supplemental] if supplemental else [])
        else:
            retrieval_hits = primary_hits or superseded_hits[:top_k]

        best_score = retrieval_hits[0].get("score", 0.0)
        threshold = self.settings.RETRIEVAL_THRESHOLD
        if float(best_score or 0.0) < float(threshold if threshold is not None else DEFAULT_RETRIEVAL_THRESHOLD):
            return {
                "answer": UNKNOWN_ANSWER,
                "sources": [],
                "found_in_documents": False,
                "confidence": "low",
            }

        sources = []
        context_parts = []
        for hit in retrieval_hits:
            payload = hit.get("payload") or {}
            document_name = payload.get("document") or payload.get("source_file") or "unknown.pdf"
            page_number = int(payload.get("page") or 1)
            excerpt = payload.get("text") or ""
            section = payload.get("section")
            revision = payload.get("revision")
            effective_date = payload.get("effective_date")
            revision_status = payload.get("revision_status") or "revision_unknown"
            superseded_by = payload.get("superseded_by")
            revision_details = [f"Status: {revision_status.replace('_', ' ')}"]
            if revision:
                revision_details.append(f"Revision: {revision}")
            if effective_date:
                revision_details.append(f"Effective date: {effective_date}")
            if superseded_by:
                revision_details.append(f"Superseded by: {superseded_by}")
            context_parts.append(
                f"[Document: {document_name} | Page {page_number} | {' | '.join(revision_details)}]\n{excerpt}"
            )
            sources.append(
                {
                    "document": document_name,
                    "page": page_number,
                    "excerpt": excerpt,
                    "score": hit.get("score"),
                    "section": section,
                    "revision": revision,
                    "effective_date": effective_date,
                    "revision_status": revision_status,
                    "superseded_by": superseded_by,
                }
            )

        context = "\n\n".join(context_parts)
        system_prompt = (
            "You are a manufacturing documentation assistant. Answer questions exclusively using the provided document context. "
            "Never use external knowledge or assumptions. If the supplied context does not contain sufficient evidence to answer, "
            "state that the information could not be found in the provided documents. Never invent operating parameters, safety instructions, "
            "maintenance procedures, measurements, or page references. If the source documents contain conflicting information, explain the conflict and cite both sources. "
            "When document revisions are available, use current effective revisions in preference to superseded ones. Do not present superseded instructions as current. "
            "If only superseded evidence answers the question, clearly state that the cited revision is superseded and name the replacement when available. "
            "Do not use documents marked not yet effective as current instructions. "
            "Keep answers concise and easy for manufacturing supervisors to understand."
        )
        user_prompt = (
            f"Question: {cleaned_question}\n\n"
            f"Context:\n{context}\n\n"
            "Answer using only the provided context. For every factual answer, cite at least one exact source "
            "using this format: [document filename, page N]. Use only filenames and page numbers present in the context. "
            "If evidence is insufficient, say so plainly instead of guessing."
        )

        response = self.llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])
        content = response.content if hasattr(response, "content") else response
        if isinstance(content, str):
            answer = content
        elif isinstance(content, list):
            answer = "\n".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in content
            )
        else:
            answer = str(content)

        grounded = not is_abstention(answer) and has_grounded_citation(answer, sources)
        confidence = "high" if grounded and best_score >= 0.25 else "medium" if grounded else "low"

        return {
            "answer": answer.strip() if grounded else UNKNOWN_ANSWER,
            "sources": sources if grounded else [],
            "found_in_documents": grounded,
            "confidence": confidence,
        }
