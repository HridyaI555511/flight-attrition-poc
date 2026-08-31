# Flight Attrition POC — Demo Walkthrough

## What This Is

This is a proof-of-concept that predicts which employees are at the highest risk of leaving — using data already sitting in SAP SuccessFactors and Payroll. No new HR surveys, no manual spreadsheets. The system pulls live data, scores every active employee against nine evidence-backed risk factors, and surfaces the results in an interactive dashboard.

---

## Step 1 — The Problem We're Solving

> *"We know attrition is expensive. But by the time someone resigns, it's already too late. The goal here is to identify flight risk 3–6 months earlier, so HR and managers can act."*

The challenge is that the signals are already in our systems — they're just scattered across SuccessFactors, Payroll, and time-management — and no one is connecting them automatically. This POC does exactly that.

---

## Step 2 — How the Data Is Collected

The system uses **Playwright** (a browser automation framework) to:

1. Log in to SAP SuccessFactors (SFSALES010044) using service credentials
2. Call the OData APIs directly — the same APIs SF uses internally
3. Pull and save 15+ data sets: employee profiles, job history, performance reviews, compensation, time/absence records, talent pool membership, job applications, and more
4. Do the same for the Payroll instance (SFSALES009656) to get actual salary and pay component data

This runs as an automated script (`npm run fetch:all`) and takes about 4 minutes. Everything lands as raw JSON in a local fixtures folder.

> *"We're not using any exports or reports — we're calling the same OData endpoints that SuccessFactors itself uses. So this could be run on any SF instance with the right credentials."*

---

## Step 3 — The Risk Model

Once the data is fetched, a Python script (`model/attrition_enriched.py`) merges it all together and scores every active employee.

**499 active employees** are scored against nine factors:

| Risk Factor | Weight | What It Measures |
|---|---|---|
| Role stagnation | 20% | Years in current role without a title/position change |
| Low performance | 20% | Missing or low performance review scores |
| Below-market salary (compa-ratio) | 15% | Salary vs. department median from Payroll data |
| Stale compensation | 13% | How long since the last pay change |
| No bonus history | 8% | No recorded bonus in the pay data |
| High absence | 8% | Above-average leave usage — a leading burnout signal |
| Manager instability | 5% | Number of manager changes in recent history |
| Short tenure | 7% | Employees still in the high-risk first-two-years window |
| Recently hired into role only | 4% | No internal mobility recorded |

The weights are grounded in published HR attrition research (compa-ratio below 0.9 correlates with 2–3× higher exit probability; role stagnation and missing performance feedback are the two strongest voluntary turnover predictors).

> *"The model isn't a black box. Every score has a plain-English explanation attached to it, so a manager can understand exactly why someone appeared on the list."*

---

## Step 4 — The Results

From today's run:

- **30 employees flagged as High Risk** (score ≥ 60)
- **431 Medium Risk**
- **38 Low Risk**
- **Average compa-ratio: 1.02** — healthy overall, but **91 employees are below 0.9** (the market danger zone)
- **71 employees have had at least one manager change** recently

The top high-risk employee — **Rebecca Watts, Leadership Team Asia Pacific** — scores 71/100. She's been in the same role for 9.7 years, has no recorded performance review, and her salary sits at a compa-ratio of 0.32 (well below her department median). Each of those factors is listed in plain language in the output.

---

## Step 5 — The Dashboard

Open **http://localhost:8080/attrition_dashboard.html**

The interactive HTML dashboard shows:

- A summary tile row (total employees, high/medium/low counts, salary coverage)
- A risk distribution chart
- A searchable, sortable table of all 499 employees with their scores and risk band
- For high-risk employees: a detailed breakdown of which factors drove their score and why it matters

> *"This is a self-contained HTML file — no server needed in production. It can be emailed, shared on a SharePoint, or embedded in a SuccessFactors tile."*

---

## Step 6 — How to Refresh It

To get the latest data and re-score:

```bash
npm run fetch:all   # pulls fresh data from SFSF + Payroll (~4 min)
npm run run         # re-runs the model and rebuilds the dashboard
```

The whole pipeline takes about 5 minutes end to end.

---

## What This Proves

This POC demonstrates that:

1. **The data is there** — SuccessFactors already holds everything needed to predict attrition. Nothing new needs to be collected.
2. **The pipeline is automatable** — fetching, scoring, and dashboarding can run on a schedule (nightly, weekly) with no manual effort.
3. **The output is actionable** — HR business partners and line managers get a ranked list with reasons, not just a score.

The natural next step is running this against a production SuccessFactors tenant, validating the risk scores against known historic leavers, and tuning the factor weights accordingly.

---

*Generated from: SAP SuccessFactors SFSALES010044 + Payroll SFSALES009656 | 499 active employees | August 2026*
