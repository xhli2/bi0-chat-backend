#!/usr/bin/env python3
"""Build a markdown report from inputs/evidence.json."""
from __future__ import annotations

import json
import os
from pathlib import Path

workspace = Path(os.environ.get("BIO_WORKSPACE", "."))
output_dir = Path(os.environ.get("BIO_OUTPUT_DIR", workspace / "outputs" / "manual"))
output_dir.mkdir(parents=True, exist_ok=True)
evidence_path = workspace / "inputs" / "evidence.json"

records: list[dict] = []
if evidence_path.exists():
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        records = [item for item in payload if isinstance(item, dict)]
    elif isinstance(payload, dict):
        records = [payload]

lines = ["# Bioinformatics Report", "", "## Findings", ""]
if not records:
    lines.append("- No structured evidence found in inputs/evidence.json")
else:
    for idx, item in enumerate(records, start=1):
        summary = item.get("summary") or item.get("title") or "No summary"
        source = item.get("source", "unknown")
        lines.append(f"{idx}. **{source}**: {summary}")

lines.extend(["", "## Evidence", ""])
for item in records:
    identifiers = item.get("identifiers") or {}
    id_text = ", ".join(f"{k}={v}" for k, v in identifiers.items()) or "n/a"
    lines.append(f"- {item.get('source', 'unknown')} ({id_text})")

lines.extend(["", "## Limitations", "", "- Automated synthesis; verify against primary sources.", ""])
report_path = output_dir / "report.md"
report_path.write_text("\n".join(lines), encoding="utf-8")
print(json.dumps({"report": str(report_path), "evidence_count": len(records)}))
