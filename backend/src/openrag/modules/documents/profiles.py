"""Immutable content-free identities for document ingestion configurations."""

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol


class IngestionProfileSettings(Protocol):
    @property
    def embedding_backend(self) -> str: ...

    @property
    def embedding_model_id(self) -> str: ...

    @property
    def embedding_dim(self) -> int: ...

    @property
    def ocr_mode(self) -> str: ...

    @property
    def ocr_languages(self) -> str: ...

    @property
    def ocr_min_confidence(self) -> float: ...

    @property
    def ocr_text_score(self) -> float: ...

    @property
    def ocr_bitmap_area_threshold(self) -> float: ...


def _identity(prefix: str, values: dict[str, object]) -> str:
    encoded = json.dumps(
        values,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return f"{prefix}/{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True, slots=True)
class IngestionProfiles:
    parser_profile_version: str
    ocr_profile_version: str
    chunking_profile_version: str
    embedding_profile_version: str
    index_profile_version: str

    def as_tuple(self) -> tuple[str, str, str, str, str]:
        return (
            self.parser_profile_version,
            self.ocr_profile_version,
            self.chunking_profile_version,
            self.embedding_profile_version,
            self.index_profile_version,
        )


def active_ingestion_profiles(settings: IngestionProfileSettings) -> IngestionProfiles:
    """Snapshot exact config identities without storing credentials or provider input."""

    return IngestionProfiles(
        parser_profile_version="openrag-parser/v1",
        ocr_profile_version=_identity(
            "ocr/v1",
            {
                "mode": settings.ocr_mode,
                "languages": settings.ocr_languages,
                "min_confidence": settings.ocr_min_confidence,
                "text_score": settings.ocr_text_score,
                "bitmap_area_threshold": settings.ocr_bitmap_area_threshold,
            },
        ),
        chunking_profile_version="openrag-page-local/v1",
        embedding_profile_version=_identity(
            "embedding/v1",
            {
                "backend": settings.embedding_backend,
                "model_id": settings.embedding_model_id,
                "dimension": settings.embedding_dim,
            },
        ),
        index_profile_version="openrag-authority-hybrid/v1",
    )
