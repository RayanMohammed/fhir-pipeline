# Extraction coverage report

Archive checked: `synthea_subset.tar.gz`
Patient bundles scanned: 2000 (0 failed extraction entirely -- handled by the DLQ, not counted below)

## In-scope Observations (height, weight, blood pressure)
- Total found in source: 79666
- Captured by extract_clinical_data(): 79666 (100.0%)
- Unexpectedly dropped: 0

## Conditions
- Total found in source: 70817
- Captured: 70817 (100.0%)
- Unexpectedly dropped: 0

## Misattribution check
- Rows found attributed to the wrong patient: 0

## Out-of-scope Observation codes (real clinical data this pipeline doesn't parse)
- Distinct out-of-scope codes seen: 234
- Total out-of-scope resources seen: 912307
- See `out_of_scope_codes.csv` for the full per-code breakdown.

No unexpected drops or misattributions found.
