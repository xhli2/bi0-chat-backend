#!/usr/bin/env python3
"""Merge JSON evidence snippets from inputs/ into a single summary under outputs/."""
from __future__ import annotations

import json
import os
from pathlib import Path

workspace = Path(os.environ.get("BIO_WORKSPACE", "."))
inputs_dir = workspace / "inputs"
output_dir = Path(os.environ.get("BIO_OUTPUT_DIR", workspace / "outputs" / "manual"))
output_dir.mkdir(parents=True, exist_ok=True)

records: list[dict] = []
for path in sorted(inputs_dir.glob("*.json")):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    if isinstance(payload, list):
        records.extend(item for item in payload if isinstance(item, dict))
    elif isinstance(payload, dict):
        records.append(payload)

summary = {
    "record_count": len(records),
    "sources": sorted({str(item.get("source", "unknown")) for item in records}),
    "records": records,
}
out_path = output_dir / "evidence_summary.json"
out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"output": str(out_path), "record_count": len(records)}))
