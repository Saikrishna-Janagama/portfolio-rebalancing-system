# Portfolio Rebalancing — Submission Notes

## Run

Python 3.10+; no third-party packages required.

From the extracted project folder:

```bash
python run_all.py --portfolios 1000 --instruments 5000 --days 250 --verify-repeat
```

For a quick smoke test:

```bash
python run_all.py --out sample_output --portfolios 20 --instruments 100 --days 15
```

The default output location is next to the scripts, independent of the VS Code working directory.

## Review the results

- `output/validation_summary.json`: row counts, integrity checks, SHA-256 hashes and (when requested) reproducibility.
- `output/additional_validation.json`: results for ten deterministic policy-review candidates.
- `output/candidate_inputs.json`: inputs to the exception checker (no expected labels).
- `output/ground_truth_injected.csv` / `.json`: **expected labels only**, stored separately from candidate inputs.
- `output/case_evaluation.json`: actual decisions and whether each case matched expected ground truth.
- `output/review_events.json`: simulated policy-review lifecycle events.
- `output/json/`: exported entity tables.
- `output/data_dictionary.csv` / `.json`: schema-derived field descriptions.
- `output/source_manifest.json`: synthetic provenance and seed.

## Limitations

- This is a deterministic educational simulation, not investment advice.
- The ten-case precision and recall are for controlled scenarios, not out-of-sample estimates.
- Review approval/rejection is simulated and not an executed brokerage transaction.
- Data dictionary validation notes are schema-level summaries, not exhaustive per-field domain specifications.
- Full generated outputs are intentionally excluded from the source ZIP; run the command above to recreate them.
