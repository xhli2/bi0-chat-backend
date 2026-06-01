# Splice Analysis

## Preconditions
- HGVS must include transcript (e.g. `NM_...`) and variant notation (`c.` / `g.`).
- Genome build must be GRCh37 or GRCh38 before SpliceAI submission.

## Script helper
Run `check_hgvs.py` with the HGVS string as the first argument to validate format locally
before calling `bio_spliceai_submit`.
