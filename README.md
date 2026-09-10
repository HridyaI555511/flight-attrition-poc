# Flight Attrition POC

A proof-of-concept that predicts employee flight risk using live data from SAP SuccessFactors and Payroll — no surveys, no manual spreadsheets. The system fetches data via OData APIs, scores every active employee against 17 evidence-backed risk factors, and surfaces results in an interactive HTML dashboard with a built-in conversational AI query interface powered by a local RAG (Retrieval-Augmented Generation) server.

---

## What It Does

- **Pulls live data** from SAP SuccessFactors (SFSALES010044) and Payroll (SFSALES009656) via the MCP server (OData v2 REST)
- **Scores active employees** across 17 weighted risk factors
- **Generates an interactive dashboard** with risk bands, factor breakdowns, plain-English explanations for every high-risk employee, and a Model Validation tab showing backtest results against historical leavers
- **Conversational AI query** via a local RAG server — ask natural-language HR questions and get answers grounded in the actual employee data
- **Runs on a daily cron schedule** via `refresh.sh` to keep the dashboard current

---

## Architecture

```
SAP SuccessFactors (OData v2)          SAP Payroll (OData v2)
        │                                       │
        └─────────── mcp-server/ ───────────────┘
                     (Node.js, Playwright SSO auth, cookie proxy)
                           │
                    fixtures/sfsf/              fixtures/payroll/
                    (raw JSON snapshots)        (raw JSON snapshots)
                           │
              model/attrition_enriched.py
              (17-factor scoring, backtest)
                           │
              fixtures/output/
              ├── all_employees_enriched_risk.csv
              ├── attrition_enriched_summary.json
              └── backtest_results.json
                           │
           ┌───────────────┴───────────────────┐
           │                                   │
  model/build_dashboard.py            model/rag_server.py
  (static HTML — Chart.js)            (Flask :5001, RAG backend)
           │                           ├── ChromaDB (semantic search)
  fixtures/output/                     ├── BM25Okapi (keyword search)
  attrition_dashboard.html             └── Claude CLI (generation)
           │                                   │
           └──────────── browser ──────────────┘
                  5-tab interactive dashboard
                  (Overview | Register | Explanations | Validation | Ask AI)
```

**RAG pipeline (Ask AI tab):**

1. User submits a question in the dashboard chat UI
2. `rag_server.py` runs a 4-layer hybrid retrieval:
   - **Metadata filter** — pre-filters by department / location / risk band keywords
   - **Semantic search** — ChromaDB `all-MiniLM-L6-v2` embeddings, top 15 results
   - **BM25 keyword search** — `rank_bm25`, top 10 results, deduped
   - **Pandas top-N boost** — for ranking queries ("top 5 highest risk"), sorts by the most relevant factor column
3. Up to 20 merged employee profiles are injected as context
4. The local `claude` CLI (Claude Code auth, no extra API key needed) generates the answer
5. Response is streamed back to the chat UI with source employee IDs

---

## Risk Factors

Weights and thresholds are configured in [`model/config.yaml`](model/config.yaml) — tune without touching model code.

| Factor | Weight | Data Source |
|---|---|---|
| Role stagnation (years in role, no title change) | 20% | `EmpJob` / `EmpJobHistory` |
| Low / missing performance rating | 20% | `PerformanceForms` |
| Below-market salary (compa-ratio vs dept median) | 12% | Payroll recurring pay |
| Stale compensation (months since last pay change) | 8% | `Compensation` |
| High absence frequency | 8% | `EmployeeTime` |
| No bonus history | 6% | Payroll non-recurring pay |
| Unmet bonus expectation (target set, no payout) | 7% | `EmpCompensation` + Payroll |
| High unused PTO balance | 5% | `TimeAccount` / `TimeAccountDetail` |
| Pay group compa-ratio (vs same comp class median) | 3% | `EmpCompensation` |
| Early career stage (age < 35) | 3% | `PerPerson` |
| Short tenure (0–2 year high-risk window) | 2% | `Employee` hireDate |
| Manager instability (# manager changes) | 2% | `EmpJobHistory` |
| Not in calibration session | 2% | `CalibrationSession` |
| Open requisitions in department | 2% | `JobRequisition` |
| Internal job applications | 0% | `JobApplication` |
| No raise since hire | 0% | `Compensation` |

---

## Project Structure

```
flight-attrition-poc/
├── mcp-server/                       # MCP server — OData proxy for Claude Code
│   ├── src/index.ts                  # TypeScript source: OAuth2 SAML Bearer + Basic Auth fallback
│   ├── dist/index.js                 # Compiled output (auto-built)
│   ├── package.json
│   └── tsconfig.json
├── model/
│   ├── attrition_enriched.py         # 17-factor risk scoring model + backtest
│   ├── build_dashboard.py            # 5-tab interactive HTML dashboard builder
│   ├── rag_server.py                 # RAG backend (Flask :5001) — ChromaDB + BM25 + Claude CLI
│   └── config.yaml                   # Factor weights, thresholds, data quality settings
├── fixtures/
│   ├── sfsf/                         # Raw SFSF OData JSON snapshots (gitignored)
│   ├── payroll/                      # Raw Payroll OData JSON snapshots (gitignored)
│   └── output/                       # Model outputs — CSV, HTML, JSON (gitignored)
├── scripts/                          # Helper shell scripts (fetch, refresh, etc.)
├── tests/                            # Legacy Playwright data-fetch scripts
├── .env.example                      # Credential template (copy to .env)
├── .claude/settings.json             # Claude Code MCP + slash command config (local only)
├── refresh.sh                        # Daily cron pipeline script
├── DEMO_WALKTHROUGH.md
├── requirements.txt                  # Python deps: pandas, chromadb, flask, rank_bm25, …
└── package.json
```

---

## Setup

### Prerequisites
- Node.js 20+
- Python 3.11+

```bash
npm install
cd mcp-server && npm install && npm run build && cd ..
pip3 install -r requirements.txt
```

### Environment Variables

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

Required variables:

```
# SAP SuccessFactors — SFSF instance
SFSF_BASE_URL=https://salesdemo.successfactors.eu
SFSF_COMPANY=SFSALES010044
SFSF_USERNAME=your_username
SFSF_PASSWORD=your_password

# OAuth2 SAML Bearer (production) — leave blank to fall back to Basic Auth
# Steps: SF Admin → OAuth2 Client Applications → Add Application
# Set "Allow unsigned assertion" = true, copy the Client ID below.
SFSF_OAUTH_CLIENT_ID=

# SAP SuccessFactors — Payroll instance
PY_BASE_URL=https://hcm44preview.sapsf.com
PY_COMPANY=SFSALES009656
PY_USERNAME=your_username
PY_PASSWORD=your_password
```

> **Authentication:** By default the MCP server uses HTTP Basic Auth. For production, set `SFSF_OAUTH_CLIENT_ID` to enable OAuth2 SAML Bearer tokens with automatic 23-hour caching and 401 retry.

---

## Usage

### Via Claude Code (MCP)

The MCP server is configured in `.claude/settings.json`. With it running, use the `/attrition` slash command in Claude Code, or ask Claude directly:

```
run the attrition model and update the dashboard
show me the high-risk employees
what's the capture rate in the backtest?
```

### Manual pipeline

**1. Fetch fresh data from SuccessFactors + Payroll**

Via MCP tool (in Claude Code):
```
fetch and save all SF entities
```

Or directly via Node:
```bash
# The MCP server exposes fetch_and_save_all — run via Claude Code
```

**2. Run the risk model and rebuild the dashboard**
```bash
python3 model/attrition_enriched.py && python3 model/build_dashboard.py
```

**3. Open the dashboard**
```bash
open fixtures/output/attrition_dashboard.html
# or serve it:
cd fixtures/output && python3 -m http.server 8080
```

**Full pipeline in one command:**
```bash
bash refresh.sh
```

---

## Ask AI (RAG Server)

The **Ask AI** dashboard tab requires the RAG server to be running alongside the dashboard.

### Start the RAG server

```bash
python3 model/rag_server.py
# Server starts on http://localhost:5001
# Indexes all employees into ChromaDB + BM25 on startup (~5s)
```

Then open the dashboard — the status dot in the Ask AI tab turns green when the server is reachable.

### Example questions

```
Who are the top 5 highest risk employees?
What's driving attrition risk in Engineering?
Which employees have low pay AND no performance review?
Show me high-risk employees in London.
Suggest retention actions for our most at-risk cohort.
```

### Sync live data from SAP

Click **"Sync from SAP"** in the Ask AI tab (or `POST http://localhost:5001/refresh`) to trigger a full live data pull → re-score → index rebuild in the background. Poll `/refresh/status` for progress.

### API endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Server status, employee count, data age |
| `/chat` | POST | `{"message": "...", "history": [...]}` → `{"response": "...", "sources": [...]}` |
| `/refresh` | POST | Trigger background SAP data fetch + re-score + index rebuild |
| `/refresh/status` | GET | Poll refresh progress and completion state |

## Dashboard Tabs

| Tab | Contents |
|---|---|
| **Overview & Charts** | KPI strip, risk donut, score histogram, factor sub-score comparison, dept ranking, weight pie |
| **Employee Risk Register** | Searchable, sortable table of all employees with inline score bars |
| **High-Risk Explanations** | Per-employee cards with plain-English reasoning and top 3 risk drivers |
| **Model Validation** | Backtest results — historical leaver score distribution vs active employees, capture rate, separation metric |
| **Ask AI** | Conversational chat powered by the local RAG server — natural-language HR queries grounded in live employee data, with suggested questions and a one-click SAP data sync button |

---

## Tuning the Model

Edit [`model/config.yaml`](model/config.yaml) — no Python changes needed:

```yaml
weights:
  f_role_stagnation: 0.20   # increase to emphasise stagnation
  f_compa_ratio:     0.12   # increase if salary is the primary retention lever

thresholds:
  compa_below_market: 0.9   # raise to 0.95 for tighter market definition
  pto_high_days: 20         # adjust to your company's accrual norm

risk_bands:
  low_max: 30
  medium_max: 60

data_quality:
  min_active_employees: 50  # model aborts if fewer employees load (data quality guard)
```

---

## Output Files

All outputs are written to `fixtures/output/` (gitignored — re-generated on each run):

| File | Description |
|---|---|
| `attrition_dashboard.html` | Self-contained interactive dashboard (4 tabs) |
| `all_employees_enriched_risk.csv` | Full scored employee list |
| `high_risk_enriched_explanations.txt` | Plain-English explanation per high-risk employee |
| `high_risk_enriched_explanations.csv` | Same, machine-readable |
| `attrition_enriched_summary.json` | Run summary — counts, factor weights, coverage stats |
| `backtest_results.json` | Backtest results — capture rate, score separation, histograms |
| `attrition_enriched.png` | Static visualisation charts |

---

## Daily Automation

`refresh.sh` runs the full pipeline (fetch → score → dashboard). Add it to cron:

```bash
(crontab -l 2>/dev/null; echo "0 7 * * * /path/to/refresh.sh") | crontab -
```

Logs are written to `refresh.log` in the project root.

For production, replace the cron job with **SAP BTP Job Scheduling Service** to run inside the BTP environment with managed credentials.

---

## Model Validation

The **Model Validation** dashboard tab shows a backtest against historical leavers (`employment.endDate`). Key metrics:

| Metric | Target | Meaning |
|---|---|---|
| **Capture rate** (High + Medium) | ≥ 70% | % of actual leavers the model would have flagged |
| **Score separation** | > +5 pts | Avg leaver score minus avg active employee score |

> **Note:** The current backtest uses a snapshot of data at the time of the run, not true time-series reconstruction. For high-confidence validation, fetch full termination history from SF Reports and run a quarterly back-test. Scores for reconstructed profiles (missing perf/job data) will default to medium — score separation is the more reliable signal.

---

## Production Readiness Checklist

- [x] Factor weights and thresholds externalised to `model/config.yaml`
- [x] `NOW` uses `datetime.now()` — no hardcoded dates
- [x] Data quality guard aborts run if < 50 employees load
- [x] OAuth2 SAML Bearer auth with 23h token caching (Basic Auth fallback)
- [x] `load()` is fault-tolerant — silently skips missing or corrupt fixture files
- [x] `.env` is in `.gitignore` — credentials never committed
- [ ] Schedule via BTP Job Scheduling Service (currently manual cron)
- [ ] Replace fixture files with live OData calls in the model
- [ ] Add RBAC — HR managers see only their department
- [ ] Add audit trail (GDPR compliance for individual risk scores)
- [ ] Containerise with Docker for portable BTP deployment

---

## Limitations

- **Model weights are research-based**, not fitted to this company's historical leavers. Use the backtest capture rate and score separation to tune `config.yaml`.
- **Compa-ratio uses internal department median** as proxy for market rate — external benchmark data (Radford, Mercer) would improve accuracy.
- **SF Goals, Learning, and Succession** modules are not yet connected — strong protective signals available via OData.
- **Backtest n is small** (18 leavers in the demo tenant) — results are directional only until more historical data is fetched.

---

## Demo

See [DEMO_WALKTHROUGH.md](DEMO_WALKTHROUGH.md) for a step-by-step guide to presenting this POC to stakeholders.
