from __future__ import annotations

import math
from threading import Lock
from typing import TYPE_CHECKING

from huggingface_hub import InferenceClient

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


class EmbeddingService:
    def __init__(self, settings):
        self.settings = settings
        self.model_name = settings.HF_EMBEDDING_MODEL
        self.api_key = settings.HUGGINGFACE_API_KEY
        self.provider = settings.HF_EMBEDDING_PROVIDER
        self._local_model: SentenceTransformer | None = None
        self._local_model_lock = Lock()

        if self.provider == "api" and not self.api_key:
            raise ValueError(
                "HUGGINGFACE_API_KEY is missing. Set it to use the hosted embedding API, or set HF_EMBEDDING_PROVIDER=local."
            )

        self.client = (
            InferenceClient(model=self.model_name, token=self.api_key)
            if self.provider == "api"
            else None
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_text(text) for text in texts]

    def embed_query(self, question: str) -> list[float]:
        return self._embed_text(question)

    def _embed_text(self, text: str) -> list[float]:
        normalized_text = text.replace("\n", " ")
        if self.provider == "local":
            model = self._get_local_model()
            response = model.encode(
                [normalized_text],
                convert_to_numpy=True,
                normalize_embeddings=False,
                show_progress_bar=False,
            )[0]
            values = response.tolist() if hasattr(response, "tolist") else response
            vector = [float(value) for value in values]
        else:
            if self.client is None:
                raise RuntimeError("Hugging Face embedding client is not initialized.")
            response = self.client.feature_extraction(text=normalized_text)
            values = response.tolist() if hasattr(response, "tolist") else response

            while values and isinstance(values[0], list) and len(values) == 1:
                values = values[0]

            if not values or not isinstance(values[0], list):
                vector = [float(value) for value in values]
            else:
                token_vectors = values
                dimension = len(token_vectors[0])
                if any(len(token_vector) != dimension for token_vector in token_vectors):
                    raise ValueError("Embedding provider returned vectors with inconsistent dimensions.")
                vector = [
                    sum(float(token_vector[index]) for token_vector in token_vectors)
                    / len(token_vectors)
                    for index in range(dimension)
                ]

        self._validate_dimensions([vector])
        magnitude = math.sqrt(sum(value * value for value in vector))
        if magnitude == 0:
            raise ValueError("Embedding provider returned a zero-length vector.")
        return [value / magnitude for value in vector]

    def _get_local_model(self) -> SentenceTransformer:
        if self._local_model is None:
            with self._local_model_lock:
                if self._local_model is None:
                    self._local_model = _load_local_sentence_transformer(
                        self.model_name,
                        token=self.api_key,
                    )
        return self._local_model

    def _validate_dimensions(self, vectors: list[list[float]]) -> None:
        if any(len(vector) != self.settings.HF_EMBEDDING_DIMENSION for vector in vectors):
            raise ValueError(
                f"Embedding model returned a vector with an unexpected size; "
                f"expected {self.settings.HF_EMBEDDING_DIMENSION} dimensions."
            )

    @property
    def dimension(self) -> int:
        return self.settings.HF_EMBEDDING_DIMENSION


def _load_local_sentence_transformer(
    model_name: str,
    *,
    token: str | None = None,
) -> SentenceTransformer:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name, token=token)
