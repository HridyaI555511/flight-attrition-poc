# Flight Attrition POC

A proof-of-concept that predicts employee flight risk using live data from SAP SuccessFactors and Payroll — no surveys, no manual spreadsheets. The system fetches data via OData APIs, scores every active employee against 11 evidence-backed risk factors, and surfaces results in an interactive HTML dashboard.

---

## What It Does

- **Pulls live data** from SAP SuccessFactors (SFSALES010044) and Payroll (SFSALES009656) using Playwright browser automation
- **Scores 499 active employees** across 11 weighted risk factors
- **Generates an interactive dashboard** with risk bands, factor breakdowns, and plain-English explanations for every high-risk employee
- **Runs on a daily cron schedule** to keep the dashboard current

---

## Risk Factors

| Factor | Weight | Data Source |
|---|---|---|
| Role stagnation (years in role, no title change) | 20% | `EmpJob` / `EmpJobHistory` |
| Low / missing performance rating | 20% | `PerformanceForms` |
| Below-market salary (compa-ratio vs dept median) | 15% | Payroll recurring pay |
| Stale compensation (months since last pay change) | 10% | `Compensation` |
| No bonus history | 8% | Payroll non-recurring pay |
| High absence frequency | 8% | `EmployeeTime` |
| Short tenure (0–2 year high-risk window) | 6% | `Employee` hireDate |
| High unused PTO balance | 5% | `TimeAccount` / `TimeAccountDetail` |
| Internal job applications | 3% | `JobApplication` |
| Manager instability (# manager changes) | 3% | `EmpJobHistory` |
| No raise since hire | 2% | `Compensation` |

---

## Project Structure

```
flight-attrition-poc/
├── tests/                        # Playwright data-fetch scripts
│   ├── sfsf-attrition-data.spec.ts
│   ├── empjob-paginated.spec.ts
│   ├── additional-signals.spec.ts
│   ├── leave-data.spec.ts
│   └── manager-org-changes.spec.ts
├── model/
│   ├── attrition_enriched.py     # Risk scoring model
│   └── build_dashboard.py        # Interactive HTML dashboard builder
├── fixtures/
│   ├── sfsf/                     # Raw SFSF OData JSON (gitignored)
│   ├── payroll/                  # Raw Payroll OData JSON (gitignored)
│   └── output/                   # Model outputs (gitignored)
├── refresh.sh                    # Daily cron script
├── DEMO_WALKTHROUGH.md           # Step-by-step demo guide
├── playwright.config.ts
├── package.json
└── requirements.txt
```

---

## Setup

### Prerequisites
- Node.js 20+
- Python 3.11+
- Playwright browsers

```bash
npm install
npx playwright install chromium
pip3 install -r requirements.txt
```

### Environment Variables

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

Required variables:
```
SFSF_BASE=https://salesdemo.successfactors.eu
SFSF_COMPANY=SFSALES010044
SFSF_USERNAME=your_username
SFSF_PASSWORD=your_password
PY_BASE=https://hcm44preview.sapsf.com
PY_COMPANY=SFSALES009656
PY_USERNAME=your_username
PY_PASSWORD=your_password
```

---

## Usage

### Fetch fresh data from SuccessFactors + Payroll
```bash
npm run fetch:all
```

### Run the risk model and rebuild the dashboard
```bash
npm run run
```

### Serve the dashboard locally
```bash
cd fixtures/output && python3 -m http.server 8080
# Open http://localhost:8080/attrition_dashboard.html
```

### Full pipeline in one command
```bash
npm run fetch:all && npm run run
```

---

## Daily Automation

A cron job runs `refresh.sh` every morning at 7:00 AM to fetch new data and rebuild the dashboard automatically:

```bash
# Install the cron job
(crontab -l 2>/dev/null; echo "0 7 * * * /path/to/refresh.sh") | crontab -
```

Logs are written to `refresh.log` in the project root.

---

## Output Files

All outputs are written to `fixtures/output/` (gitignored — re-generated on each run):

| File | Description |
|---|---|
| `attrition_dashboard.html` | Self-contained interactive dashboard |
| `all_employees_enriched_risk.csv` | Full scored employee list |
| `high_risk_enriched_explanations.txt` | Plain-English explanation per high-risk employee |
| `high_risk_enriched_explanations.csv` | Same as above, machine-readable |
| `attrition_enriched_summary.json` | Run summary with counts and factor weights |
| `attrition_enriched.png` | Static visualisation charts |

---

## Limitations & Next Steps

- **Model weights are research-based**, not fitted to this company's historical leavers. Validate against known past attrition to tune weights.
- **Compa-ratio uses internal department median** as a proxy for market rate — external benchmark data (Radford, Mercer) would improve accuracy.
- **Peer attrition rate** (contagion effect within teams) is not yet included — attrited employees are not in the active SF export.
- **SF Goals, Learning, and Succession** modules are not yet connected — these are strong protective/risk signals available via OData.

---

## Demo

See [DEMO_WALKTHROUGH.md](DEMO_WALKTHROUGH.md) for a step-by-step guide to presenting this POC to stakeholders.
