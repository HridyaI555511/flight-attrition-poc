---
name: run-attrition-model
description: Run the enriched attrition risk scoring model against fetched fixture data
---

Execute the Python risk model to score all active employees and produce output CSVs.

## Prerequisites
- Fixture data already fetched (run `fetch-attrition-data` skill first)
- Python dependencies installed: `pip install -r requirements.txt`

## Run the enriched model

```bash
python3 model/attrition_enriched.py
```

**Outputs written to `fixtures/output/`:**
| File | Description |
|------|-------------|
| `all_employees_enriched_risk.csv` | All 499 active employees with risk scores |
| `high_risk_enriched_explanations.csv` | Top-3 factor breakdown for High-risk employees |
| `high_risk_enriched_explanations.txt` | Human-readable explanations |
| `attrition_enriched_summary.json` | Summary stats (band counts, avg score) |
| `attrition_enriched.png` | Distribution chart |

## Model factors (weights sum to 1.0)

| Factor | Weight | Signal |
|--------|--------|--------|
| Role Stagnation | 20% | Years in same role |
| Low Performance | 20% | Latest performance rating |
| Compa-Ratio | 15% | Salary vs. peer median |
| Stale Compensation | 13% | Months since last raise |
| Absence Pattern | 8% | Approved absence event count |
| No Bonus | 8% | Missing bonus payment |
| Short Tenure | 7% | < 2 years at company |
| Hire-Only | 4% | Never promoted/transferred |
| Manager Instability | 5% | Manager change count |

## Risk bands
- **High**: score ≥ 60
- **Medium**: 30 ≤ score < 60
- **Low**: score < 30

## Adjusting weights
Edit `WEIGHTS` dict at the top of `model/attrition_enriched.py`. Weights must sum to 1.0.
