---
name: rebuild-dashboard
description: Regenerate the self-contained interactive HTML attrition dashboard
---

Rebuild the HTML dashboard from the latest model output CSVs/JSON.

## Prerequisites
- Model has been run: `python3 model/attrition_enriched.py`
- Output files exist in `fixtures/output/`

## Run

```bash
python3 model/build_dashboard.py
```

**Output:** `fixtures/output/attrition_dashboard.html`

Open it:
```bash
open fixtures/output/attrition_dashboard.html
```

## Full refresh (fetch → model → dashboard)

```bash
npm run fetch:all && npm run run
```
Or step by step:
```bash
npm run fetch:sfsf
npm run fetch:payroll
npm run fetch:empjob
npm run fetch:leave
npm run fetch:signals
npm run model
npm run dashboard
open fixtures/output/attrition_dashboard.html
```

## Dashboard features
- **Overview tab**: KPI cards (High/Medium/Low counts, manager change count), risk distribution pie, factor radar, dept heatmap
- **Risk Register tab**: Filterable employee table with risk band, score, top-3 factors
- **Analytics tab**: Factor contribution bar chart, weight doughnut, dept avg risk bar
- **High-Risk Cards tab**: Detailed cards for each High-risk employee with factor breakdown badges

## Modifying the dashboard
The dashboard is generated entirely by `model/build_dashboard.py`. The HTML/JS template is embedded as a Python f-string. Edit the template section (search for `html = f"""`) to customize layout, colours, or charts.
