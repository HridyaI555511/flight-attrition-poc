Use the `sf-attrition` MCP server to fulfil the user's request. The argument is: $ARGUMENTS

Map the argument to the right action:

- **fetch** or **refresh** or **data** → call `fetch_and_save_all` to pull fresh data from SAP SuccessFactors, then `run_model` to re-score and rebuild the dashboard.
- **run** or **model** or **score** → call `run_model` to re-run the Python attrition model and rebuild the HTML dashboard.
- **dashboard** only → call `run_model` with `dashboard_only: true` to rebuild the dashboard without re-scoring.
- **summary** or **status** → call `get_risk_summary` and display the results clearly: total employees, risk band counts, coverage %, factor weights.
- **high risk** or **top** → call `get_high_risk_employees` (default top 20) and present the list with name, department, risk score, and salary.
- **login** or **auth** → call `authenticate` to refresh the SAP SuccessFactors session.
- **fetch <EntityName>** → call `fetch_entity` with the named entity (e.g. "fetch Employee", "fetch EmpCompensation").
- No argument or **help** → show the available commands:
  - `/attrition fetch` — pull fresh data from SF + re-run model
  - `/attrition run` — re-run model with existing data
  - `/attrition dashboard` — rebuild dashboard only
  - `/attrition summary` — show latest risk summary
  - `/attrition high risk` — show top 20 high-risk employees
  - `/attrition auth` — refresh SF session
  - `/attrition fetch <Entity>` — fetch a specific OData entity

After any fetch or model run, always call `get_risk_summary` and show the key numbers: total employees, high/medium/low counts, salary coverage %, and avg risk score.
