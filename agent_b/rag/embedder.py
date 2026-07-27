"""Local, no-egress text embeddings for the RAG index (S-B6, AD-31, D-43).

`Embedder` is the seam the index and retriever depend on; the offline unit suite injects a deterministic
fake, so the suite neither downloads a model nor hits the network. `FastEmbedEmbedder` is the real
implementation — fastembed runs a small ONNX model on CPU (no torch), which is what keeps embeddings
inside the 1 GB box (AD-21). The model is loaded lazily on first use so merely importing this module
(or running the fake-backed tests) costs nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of texts into vectors. All vectors share one dimensionality."""
        ...


class FastEmbedEmbedder:
    """fastembed-backed embeddings (ONNX, CPU). The model downloads once on first use (network)."""

    __slots__ = ("_model", "_model_name")

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._model: object | None = None

    def _ensure(self) -> object:
        if self._model is None:
            from fastembed import TextEmbedding  # lazy — heavy import, avoided in the offline suite

            self._model = TextEmbedding(model_name=self._model_name)
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        model = self._ensure()
        return [[float(x) for x in vector] for vector in model.embed(list(texts))]  # type: ignore[attr-defined]
