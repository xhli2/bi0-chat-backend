# VCF Quality Control

## Inputs
- Place VCF or VCF.gz under `inputs/` (e.g. `inputs/sample.vcf`).

## Run
```
bio_script_runner script_name=vcf_qc.py runtime=python args=["inputs/sample.vcf"]
```

## Interpretation
- `PASS` — header and required columns present.
- `WARN` — missing optional fields or empty variant body.
- `FAIL` — unreadable file or missing `#CHROM` header.
