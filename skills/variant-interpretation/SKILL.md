# Variant Interpretation

## Workflow
1. Normalize HGVS / gene identifiers before any external lookup.
2. Query NCBI (ClinVar/PubMed) and UniProt for supporting evidence.
3. If splice impact is relevant, submit SpliceAI and wait for job completion.
4. Run `summarize_evidence.py` to merge tool outputs into a single JSON summary under `outputs/`.

## Output contract
- List each evidence source with `retrieved_at` timestamp.
- Separate **supported**, **conflicting**, and **missing** evidence.
- Never claim pathogenicity without citing at least one primary source.
