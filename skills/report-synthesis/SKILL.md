# Report Synthesis

## Inputs
- Write a JSON file to `inputs/evidence.json` with a list of evidence objects
  (`source`, `summary`, `identifiers`, `confidence`).

## Run
```
bio_script_runner script_name=build_report.py runtime=python
```

## Output
- Markdown report at `outputs/<run>/report.md` with sections: Findings, Evidence, Limitations.
