#!/usr/bin/env python3
"""Minimal VCF QC: header, column count, and variant line sanity."""
from __future__ import annotations

import gzip
import sys
from pathlib import Path


def _open_vcf(path: Path):
    if path.suffix == ".gz" or str(path).endswith(".vcf.gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: vcf_qc.py <path-to-vcf>")
        return 2
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"FAIL: file not found: {path}")
        return 1

    status = {"PASS": 0, "WARN": 0, "FAIL": 0}
    header_cols: list[str] = []
    variant_lines = 0

    with _open_vcf(path) as handle:
        for line in handle:
            if line.startswith("##"):
                continue
            if line.startswith("#CHROM"):
                header_cols = line.strip().split("\t")
                status["PASS"] += 1
                continue
            if line.startswith("#"):
                continue
            variant_lines += 1
            cols = line.rstrip("\n").split("\t")
            if len(header_cols) and len(cols) != len(header_cols):
                status["WARN"] += 1

    if not header_cols:
        status["FAIL"] += 1
        print("FAIL: missing #CHROM header")
    elif variant_lines == 0:
        status["WARN"] += 1
        print("WARN: no variant records")
    else:
        status["PASS"] += 1
        print(f"PASS: {variant_lines} variant line(s), {len(header_cols)} columns")

    print(f"summary PASS={status['PASS']} WARN={status['WARN']} FAIL={status['FAIL']}")
    return 0 if status["FAIL"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
