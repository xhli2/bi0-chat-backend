from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


EntityType = Literal["gene", "variant", "protein", "literature", "splice_score", "job"]


class BioEvidence(BaseModel):
    source: str
    entity_type: EntityType
    identifiers: dict[str, str] = Field(default_factory=dict)
    genome_build: str | None = None
    retrieved_at: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    summary: str | None = None
    raw_ref: str | None = None


class BioEvidencePack(BaseModel):
    items: list[BioEvidence] = Field(default_factory=list)

    def add(self, item: BioEvidence) -> None:
        self.items.append(item)

    def to_context_block(self, max_items: int = 8) -> str:
        lines: list[str] = []
        for item in self.items[:max_items]:
            id_part = ", ".join(f"{key}={value}" for key, value in sorted(item.identifiers.items()))
            lines.append(
                f"- {item.entity_type}@{item.source} [{id_part}] retrieved_at={item.retrieved_at} "
                f"confidence={item.confidence}"
                + (f" summary={item.summary}" if item.summary else "")
            )
        return "[BioEvidence]\n" + "\n".join(lines) if lines else ""


def evidence_from_dict(payload: dict[str, Any]) -> BioEvidence | None:
    if not isinstance(payload, dict):
        return None
    if "source" not in payload or "entity_type" not in payload or "retrieved_at" not in payload:
        return None
    try:
        return BioEvidence.model_validate(payload)
    except Exception:
        return None
