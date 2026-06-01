Tags: vcf, qc, quality, 质控

# VCF QC Domain Pack

- Expect `#CHROM` header and consistent column counts.
- gzip `.vcf.gz` is supported; place files under workspace `inputs/`.
- Use `vcf_qc.py` via `bio_script_runner`; do not parse large VCFs in LLM context.
- Summarize PASS/WARN/FAIL counts only; do not dump raw variant rows.
