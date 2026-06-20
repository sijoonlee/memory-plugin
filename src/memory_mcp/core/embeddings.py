from __future__ import annotations

from typing import Protocol


class Embedder(Protocol):
    def embed_text(self, text: str) -> list[float]:
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        ...


class NoopEmbedder:
    """An embedder for read paths that never embed (e.g. the catalog, which only
    reads SQLite). Avoids loading the heavy embedding model; raises if anything
    unexpectedly tries to embed."""

    def embed_text(self, text: str) -> list[float]:
        raise RuntimeError("NoopEmbedder cannot embed text")

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("NoopEmbedder cannot embed text")


class LangChainHuggingFaceEmbedder:
    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        *,
        normalize_embeddings: bool = True,
        device: str | None = None,
    ) -> None:
        from langchain_huggingface import HuggingFaceEmbeddings

        model_kwargs = {}
        if device:
            model_kwargs["device"] = device

        self._embeddings = HuggingFaceEmbeddings(
            model_name=model_name,
            model_kwargs=model_kwargs,
            encode_kwargs={"normalize_embeddings": normalize_embeddings},
        )

    def embed_text(self, text: str) -> list[float]:
        return list(self._embeddings.embed_query(text))

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [list(vector) for vector in self._embeddings.embed_documents(texts)]
