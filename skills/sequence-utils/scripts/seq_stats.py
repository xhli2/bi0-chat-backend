#!/usr/bin/env python3
"""Lightweight FASTA sequence statistics (no external deps)."""
from __future__ import annotations

from pathlib import Path


def read_fasta(path: Path) -> str:
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(">"):
            continue
        lines.append(stripped.upper())
    return "".join(lines)


def main() -> int:
    candidates = [Path("inputs/seq.fasta"), Path("inputs/sequence.fasta")]
    seq_path = next((p for p in candidates if p.exists()), None)
    if seq_path is None:
        print("FAIL: place FASTA at inputs/seq.fasta")
        return 2
    seq = read_fasta(seq_path)
    if not seq:
        print("FAIL: empty sequence")
        return 1
    counts = {base: seq.count(base) for base in "ATGCN"}
    gc = counts["G"] + counts["C"]
    length = len(seq)
    gc_pct = (gc / length * 100) if length else 0.0
    print(f"PASS length={length} gc_percent={gc_pct:.2f}")
    print(f"A={counts['A']} T={counts['T']} G={counts['G']} C={counts['C']} N={counts['N']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
