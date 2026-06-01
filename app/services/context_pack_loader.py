from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings

_PACK_ROOT = Path(__file__).resolve().parents[2] / "context_packs"
_TAG_RE = re.compile(r"^Tags:\s*(.+)$", re.MULTILINE | re.IGNORECASE)


@dataclass
class ContextPackMatch:
    pack_id: str
    path: Path
    score: int
    tags: list[str]


def _parse_tags(content: str) -> list[str]:
    match = _TAG_RE.search(content)
    if not match:
        return []
    return [tag.strip().lower() for tag in match.group(1).split(",") if tag.strip()]


def _list_pack_files() -> list[Path]:
    if not _PACK_ROOT.exists():
        return []
    return sorted(_PACK_ROOT.rglob("*.md"))


def _pack_id(path: Path) -> str:
    return str(path.relative_to(_PACK_ROOT)).replace("\\", "/").removesuffix(".md")


def score_context_pack(
    *,
    pack_path: Path,
    prompt: str,
    agent_type: str,
    tenant_id: str,
    skill_names: list[str] | None = None,
) -> ContextPackMatch:
    content = pack_path.read_text(encoding="utf-8")
    tags = _parse_tags(content)
    lowered_prompt = prompt.lower()
    score = 0

    pack_id = _pack_id(pack_path)
    if pack_id.startswith("global/"):
        score += 1
    if pack_id.startswith(f"tenants/{tenant_id}/"):
        score += 5
    if agent_type in {"research", "supervisor", "orchestrator"} and pack_id.startswith("domains/"):
        score += 2

    for tag in tags:
        if tag and tag in lowered_prompt:
            score += 3

    domain_keywords = {
        "variant-analysis": ("variant", "hgvs", "pathogenic", "致病", "变异", "splice"),
        "protein-lookup": ("uniprot", "protein", "accession", "蛋白"),
        "splice-analysis": ("splice", "spliceai", "剪接", "canonical"),
        "vcf-qc": ("vcf", "quality", "qc", "质控"),
        "report-synthesis": ("report", "synthesis", "summary", "报告"),
        "cohort-gene-search": ("cohort", "gene panel", "multi-gene", "panel"),
        "web-search": ("web", "http", "fetch", "url", "网页", "链接"),
        "session-recall": ("history", "previous", "earlier", "会话", "历史"),
        "mcp-bridge": ("mcp", "plugin", "external tool", "插件"),
    }
    domain = pack_path.stem
    for keyword in domain_keywords.get(domain, ()):
        if keyword in lowered_prompt:
            score += 2

    if skill_names:
        from app.agent.skill_specs import get_skill_spec

        for skill_name in skill_names:
            spec = get_skill_spec(skill_name)
            if spec is None:
                continue
            for linked_pack in spec.context_pack_ids:
                linked_stem = linked_pack.rsplit("/", 1)[-1]
                if linked_stem == domain or linked_pack.endswith(f"/{domain}"):
                    score += 5

    return ContextPackMatch(pack_id=pack_id, path=pack_path, score=score, tags=tags)


def select_context_packs(
    *,
    prompt: str,
    agent_type: str,
    tenant_id: str,
    max_packs: int = 2,
    skill_names: list[str] | None = None,
) -> list[ContextPackMatch]:
    settings = get_settings()
    max_packs = max(1, min(max_packs, settings.context_pack_max_selected))
    matches = [
        score_context_pack(
            pack_path=path,
            prompt=prompt,
            agent_type=agent_type,
            tenant_id=tenant_id,
            skill_names=skill_names,
        )
        for path in _list_pack_files()
    ]
    global_base = next((item for item in matches if item.pack_id == "global/base"), None)
    ranked = sorted(
        [item for item in matches if item.score > 0 and item.pack_id != "global/base"],
        key=lambda item: (-item.score, item.pack_id),
    )
    selected = ranked[:max_packs]
    if global_base is not None:
        return [global_base, *selected]
    return selected[:max_packs]


def load_context_pack_text(
    *,
    prompt: str,
    agent_type: str,
    tenant_id: str,
    max_chars: int | None = None,
    skill_names: list[str] | None = None,
) -> tuple[str, list[str]]:
    settings = get_settings()
    max_chars = max_chars or settings.context_pack_max_chars
    selected = select_context_packs(
        prompt=prompt,
        agent_type=agent_type,
        tenant_id=tenant_id,
        skill_names=skill_names,
    )
    blocks: list[str] = []
    pack_ids: list[str] = []
    used = 0
    for match in selected:
        content = match.path.read_text(encoding="utf-8")
        content = _TAG_RE.sub("", content).strip()
        if not content:
            continue
        if used + len(content) > max_chars:
            remaining = max(0, max_chars - used)
            if remaining <= 0:
                break
            content = content[:remaining]
        blocks.append(f"[ContextPack:{match.pack_id}]\n{content}")
        pack_ids.append(match.pack_id)
        used += len(content)
    return "\n\n".join(blocks), pack_ids
