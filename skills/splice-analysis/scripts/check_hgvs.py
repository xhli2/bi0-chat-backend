#!/usr/bin/env python3
"""Lightweight HGVS format check for splice workflows."""
from __future__ import annotations

import re
import sys

_HGVS = re.compile(
    r"^(?:NC_|NM_|NP_|NR_|ENST|chr)?[A-Za-z0-9._:-]+(?::c\.|\.c\.|:g\.|\.g\.|:p\.|\.p\.|:n\.|\.n\.)[A-Za-z0-9*>+\-=\(\)]+$",
    re.IGNORECASE,
)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: check_hgvs.py <HGVS>")
        return 2
    candidate = sys.argv[1].strip()
    if len(candidate) < 5:
        print("INVALID: too short")
        return 1
    if _HGVS.match(candidate) or (":" in candidate and any(x in candidate for x in ("c.", "g.", "p."))):
        print(f"VALID: {candidate}")
        return 0
    print(f"INVALID: {candidate}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
