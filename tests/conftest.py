from __future__ import annotations


class FakeEmbedder:
    def embed_text(self, text: str) -> list[float]:
        lowered = text.lower()
        return [
            1.0 if "pytest" in lowered or "test" in lowered else 0.0,
            1.0 if "sdk" in lowered or "openapi" in lowered else 0.0,
            1.0,
        ]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_text(text) for text in texts]
