from pydantic import BaseModel, Field, field_validator


class QuestionRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Question to answer from the PDF documents",
    )
    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Question cannot be blank.")
        return cleaned

class SourceEvidence(BaseModel):
    document: str
    page: int | None = None
    excerpt: str
    score: float | None = None
    section: str | None = None
    revision: str | None = None
    effective_date: str | None = None
    revision_status: str | None = None
    superseded_by: str | None = None


class AskResponse(BaseModel):
    answer: str
    sources: list[SourceEvidence] = Field(default_factory=list)
    found_in_documents: bool
    confidence: str = "low"


class IngestRequest(BaseModel):
    reindex: bool = False


class IngestResponse(BaseModel):
    status: str
    message: str
    pdf_count: int
    chunks_indexed: int
    documents: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class UploadResponse(BaseModel):
    status: str
    message: str
    document: str
    job_id: str | None = None
    chunks_indexed: int = 0


class HealthResponse(BaseModel):
    status: str
    backend: str
    embeddings_configured: bool
    llm_configured: bool
    qdrant_ready: bool
