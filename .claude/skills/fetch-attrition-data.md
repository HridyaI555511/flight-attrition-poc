---
name: fetch-attrition-data
description: Fetch all SAP SuccessFactors OData data needed for the attrition model
---

Run all Playwright data-extraction tests to refresh fixture files from both SF instances.

## Prerequisites
- `.env` populated (copy `.env.example`, fill in credentials)
- `npm install` run once in the project root

## Steps

1. **Fetch SFSF base data** (employees, employment, compensation, performance):
   ```bash
   npm run fetch:sfsf
   ```

2. **Fetch Payroll base data** (employees, pay, non-recurring payments):
   ```bash
   npm run fetch:payroll
   ```

3. **Fetch EmpJob history** (manager/org changes — paginated, ~4000+ records):
   ```bash
   npm run fetch:empjob
   ```

4. **Fetch leave/absence data** (EmployeeTime, TimeAccount from both instances):
   ```bash
   npm run fetch:leave
   ```

5. **Fetch additional signals** (EmployeeTime recent, JobApplication, CompetencyRating):
   ```bash
   npm run fetch:signals
   ```

6. **Or fetch everything at once**:
   ```bash
   npm run fetch:all
   ```

## Notes
- Tests use `actionTimeout: 90000` — SFSF login can be slow
- Fixture files are written to `fixtures/sfsf/` and `fixtures/payroll/`
- These directories are gitignored; data must be re-fetched per environment
- EmpJob paginated test requires `--timeout=300000` (already set in npm script)
