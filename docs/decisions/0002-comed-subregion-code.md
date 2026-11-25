# 0002 — ComEd subregion code in EIA-930

- **Status:** Proposed — fill in after running `make find-comed`
- **Date:** YYYY-MM-DD

## Context

EIA-930 reports PJM demand split into subregions, each with a short code. We need the exact
code for ComEd and the date its data begins before writing ingestion.

## Decision

ComEd subregion code: `____` (parent BA: `PJM`). First available hour: `____`.

Set this value in `configs/model.yaml` under `eia.subregion`.

## How it was found

Output of `make find-comed` (paste the relevant lines):

```
```

## Consequences

Ingestion (Phase 1) filters on `facets[parent][]=PJM` and `facets[subba][]=<code>`.
