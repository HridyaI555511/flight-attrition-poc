"""
Build self-contained interactive HTML dashboard for flight attrition POC.
Reads the CSV / JSON outputs of attrition_enriched.py and embeds them.
"""
import json, math, pandas as pd, numpy as np
from pathlib import Path

FIXTURES = Path(__file__).parent.parent / 'fixtures' / 'output'

df     = pd.read_csv(FIXTURES / 'all_employees_enriched_risk.csv')
expl   = pd.read_csv(FIXTURES / 'high_risk_enriched_explanations.csv')
summary = json.loads((FIXTURES / 'attrition_enriched_summary.json').read_text())

# ── helpers ────────────────────────────────────────────────────────────────────
def fmt(v, decimals=1):
    if v is None or (isinstance(v, float) and math.isnan(v)): return '—'
    if isinstance(v, (int, float)):
        return f'{v:,.{decimals}f}'
    return str(v)

def pct(n, total): return f'{n/total*100:.0f}%'

# Department avg risk for chart
dept_risk = (df.groupby('department')['risk_score'].mean()
               .sort_values(ascending=False).head(12).round(1))

# Factor weights
WEIGHTS = summary['factor_weights']
FACTOR_LABELS = {
    'f_role_stagnation': 'Role Stagnation',
    'f_low_perf':        'Low Performance',
    'f_compa_ratio':     'Below Market Pay',
    'f_stale_comp':      'Stale Compensation',
    'f_only_hire':       'No Raise Since Hire',
    'f_short_tenure':    'Short Tenure',
    'f_no_bonus':        'No Bonus History',
    'f_high_absence':    'High Absence',
    'f_mgr_instability': 'Manager Instability',
}

# Risk score histogram buckets
bins = list(range(0, 105, 5))
labels = [f'{b}-{b+5}' for b in bins[:-1]]
high_hist   = [0]*len(labels)
med_hist    = [0]*len(labels)
low_hist    = [0]*len(labels)
for _, row in df.iterrows():
    score = row['risk_score']
    idx = min(int(score // 5), len(labels)-1)
    band = row['risk_band']
    if band == 'High':   high_hist[idx] += 1
    elif band == 'Medium': med_hist[idx] += 1
    else:                  low_hist[idx] += 1

# Build employee register JSON
emp_records = []
for _, row in df.iterrows():
    emp_records.append({
        'userId':       str(row['userId']),
        'name':         f"{row['firstName']} {row['lastName']}",
        'dept':         str(row['department']) if pd.notna(row['department']) else '—',
        'division':     str(row['division']) if pd.notna(row['division']) else '—',
        'location':     str(row['location']) if pd.notna(row['location']) else '—',
        'tenure':       round(float(row['tenure_years']), 1) if pd.notna(row['tenure_years']) else None,
        'roleTenure':   round(float(row['tenure_in_role_years']), 1) if pd.notna(row['tenure_in_role_years']) else None,
        'perf':         round(float(row['perf_rating']), 1) if pd.notna(row['perf_rating']) else None,
        'mthsSinceRaise': round(float(row['months_since_comp_change']), 0) if pd.notna(row['months_since_comp_change']) else None,
        'salary':       int(row['base_salary']) if pd.notna(row['base_salary']) else None,
        'currency':     str(row['currency']) if pd.notna(row['currency']) else '',
        'compaRatio':   round(float(row['compa_ratio']), 3) if pd.notna(row['compa_ratio']) else None,
        'hasBonus':     bool(row['has_bonus']),
        'absenceCount': int(row['absence_count']) if pd.notna(row['absence_count']) else 0,
        'mgrChanges':   int(row['mgr_change_count']) if pd.notna(row.get('mgr_change_count')) else 0,
        'orgChanges':   int(row['org_change_count']) if pd.notna(row.get('org_change_count')) else 0,
        'titleChanges': int(row['title_change_count']) if pd.notna(row.get('title_change_count')) else 0,
        'riskScore':    round(float(row['risk_score']), 1),
        'riskBand':     str(row['risk_band']),
        'onlyHire':     bool(row.get('only_hire_comp', 0)),
    })

# Build high-risk cards JSON (merge explanations with main df)
merged = expl.merge(
    df[['userId','tenure_years','tenure_in_role_years','perf_rating',
        'months_since_comp_change','base_salary','currency','compa_ratio',
        'has_bonus','bonus_events','absence_count','dept_median_salary' if 'dept_median_salary' in df.columns else 'department']],
    on='userId', how='left'
) if 'userId' in expl.columns else expl.copy()

high_cards = []
for _, row in expl.iterrows():
    # Look up full data row
    emp = df[df['userId'].astype(str) == str(row['userId'])]
    e = emp.iloc[0] if len(emp) > 0 else pd.Series()
    high_cards.append({
        'rank':       int(row['rank']),
        'userId':     str(row['userId']),
        'name':       str(row['name']),
        'dept':       str(row['department']) if pd.notna(row['department']) else '—',
        'riskScore':  round(float(row['risk_score']), 1),
        'salary':     int(row['base_salary']) if pd.notna(row['base_salary']) else None,
        'compaRatio': round(float(row['compa_ratio']), 3) if pd.notna(row['compa_ratio']) else None,
        'currency':   str(e.get('currency','')) if len(e) > 0 and pd.notna(e.get('currency')) else '',
        'tenure':     round(float(e.get('tenure_years', 0)), 1) if len(e) > 0 and pd.notna(e.get('tenure_years')) else None,
        'roleTenure': round(float(e.get('tenure_in_role_years', 0)), 1) if len(e) > 0 and pd.notna(e.get('tenure_in_role_years')) else None,
        'perf':       round(float(e.get('perf_rating', 0)), 1) if len(e) > 0 and pd.notna(e.get('perf_rating')) else None,
        'mthsRaise':  round(float(e.get('months_since_comp_change', 0)), 0) if len(e) > 0 and pd.notna(e.get('months_since_comp_change')) else None,
        'hasBonus':   bool(e.get('has_bonus', 0)) if len(e) > 0 else False,
        'absenceCount': int(e.get('absence_count', 0)) if len(e) > 0 and pd.notna(e.get('absence_count')) else 0,
        'mgrChanges':  int(e.get('mgr_change_count', 0)) if len(e) > 0 and pd.notna(e.get('mgr_change_count')) else 0,
        'titleChanges': int(e.get('title_change_count', 0)) if len(e) > 0 and pd.notna(e.get('title_change_count')) else 0,
        'explanation_1': str(row['explanation_1']),
        'explanation_2': str(row['explanation_2']),
        'explanation_3': str(row['explanation_3']),
    })

# ── compute factor sub-scores for each high-risk employee (for factor tags) ───
def factor_score(emp_row):
    def safe(v, d=0): return d if (v is None or (isinstance(v, float) and math.isnan(v))) else v
    scores = {
        'f_role_stagnation': min(safe(emp_row.get('roleTenure') or emp_row.get('tenure_in_role_years'), 0) / 5 * 100, 100),
        'f_low_perf':        60 if emp_row.get('perf') is None else max(0, min((5 - safe(emp_row.get('perf'), 3)) / 4 * 100, 100)),
        'f_compa_ratio':     50 if emp_row.get('compaRatio') is None else max(0, min((1.1 - safe(emp_row.get('compaRatio'), 1.0)) / 0.6 * 100, 100)),
        'f_stale_comp':      min(safe(emp_row.get('mthsRaise') or emp_row.get('mthsSinceRaise'), 36) / 36 * 100, 100),
        'f_only_hire':       100 if emp_row.get('onlyHire') else 0,
        'f_short_tenure':    max(0, (2 - min(safe(emp_row.get('tenure'), 0), 2)) / 2 * 100),
        'f_no_bonus':        0 if emp_row.get('hasBonus') else 70,
        'f_high_absence':    min(safe(emp_row.get('absenceCount'), 0) / 10 * 100, 100),
        'f_mgr_instability': min(safe(emp_row.get('mgrChanges'), 0) / 3 * 100, 100),
    }
    weighted = {k: scores[k] * WEIGHTS.get(k, 0) for k in scores}
    top3 = sorted(weighted.items(), key=lambda x: x[1], reverse=True)[:3]
    return [{'key': k, 'label': FACTOR_LABELS.get(k, k), 'score': round(v, 1)} for k, v in top3]

# Enrich high_cards with factor tags
emp_dict = {str(r['userId']): r for r in emp_records}
for card in high_cards:
    emp = emp_dict.get(str(card['userId']), {})
    merged_emp = {**emp, **card}
    card['topFactors'] = factor_score(merged_emp)

# ── write HTML ─────────────────────────────────────────────────────────────────
H = summary['risk_bands']['high']
M = summary['risk_bands']['medium']
L = summary['risk_bands']['low']
TOTAL = summary['total_active_employees']

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Flight Attrition Risk Dashboard — SAP SuccessFactors POC</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Segoe UI',Arial,sans-serif;background:#f0f2f5;color:#333}}
  .header{{background:linear-gradient(135deg,#0070f3,#004a9f);color:#fff;padding:20px 30px}}
  .header h1{{font-size:22px;font-weight:700}}
  .header p{{font-size:13px;opacity:.85;margin-top:4px}}
  .tabs{{display:flex;background:#fff;border-bottom:2px solid #e0e0e0;padding:0 24px}}
  .tab{{padding:14px 22px;cursor:pointer;font-size:14px;font-weight:600;color:#666;border-bottom:3px solid transparent;margin-bottom:-2px;transition:.2s}}
  .tab.active{{color:#0070f3;border-color:#0070f3}}
  .tab:hover{{color:#0070f3}}
  .panel{{display:none;padding:24px}}
  .panel.active{{display:block}}
  .kpi-row{{display:flex;gap:16px;margin-bottom:24px;flex-wrap:wrap}}
  .kpi{{background:#fff;border-radius:10px;padding:18px 22px;flex:1;min-width:160px;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
  .kpi-val{{font-size:32px;font-weight:800}}
  .kpi-label{{font-size:12px;color:#666;margin-top:4px}}
  .kpi.high .kpi-val{{color:#e74c3c}}
  .kpi.med  .kpi-val{{color:#f39c12}}
  .kpi.low  .kpi-val{{color:#27ae60}}
  .kpi.neu  .kpi-val{{color:#2c3e50}}
  .charts-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}}
  .chart-card{{background:#fff;border-radius:10px;padding:20px;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
  .chart-card.wide{{grid-column:1/-1}}
  .chart-card h3{{font-size:14px;font-weight:700;color:#2c3e50;margin-bottom:14px}}
  canvas{{max-height:280px}}
  /* Register */
  .controls{{display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap;align-items:center}}
  .controls input,.controls select{{padding:8px 12px;border:1px solid #ddd;border-radius:6px;font-size:13px;background:#fff}}
  .controls input{{min-width:220px}}
  table{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08);font-size:13px}}
  thead th{{background:#2c3e50;color:#fff;padding:10px 12px;text-align:left;cursor:pointer;user-select:none;white-space:nowrap}}
  thead th:hover{{background:#34495e}}
  tbody tr:nth-child(even){{background:#f8f9fa}}
  tbody tr:hover{{background:#e8f4fd}}
  td{{padding:9px 12px;vertical-align:middle}}
  .badge{{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:700}}
  .badge.High{{background:#fde8e8;color:#c0392b}}
  .badge.Medium{{background:#fef3e2;color:#d68910}}
  .badge.Low{{background:#e9f7ef;color:#1e8449}}
  .score-bar-wrap{{display:flex;align-items:center;gap:6px}}
  .score-bar{{height:8px;border-radius:4px;min-width:4px}}
  /* Cards */
  .filters-row{{display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap;align-items:center}}
  .filters-row select,.filters-row input{{padding:8px 12px;border:1px solid #ddd;border-radius:6px;font-size:13px;background:#fff}}
  .cards-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(500px,1fr));gap:20px}}
  .card{{background:#fff;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,.1);overflow:hidden}}
  .card-header{{padding:14px 18px;display:flex;justify-content:space-between;align-items:center}}
  .card-header.High{{background:linear-gradient(90deg,#e74c3c,#c0392b);color:#fff}}
  .card-header.Medium{{background:linear-gradient(90deg,#f39c12,#d68910);color:#fff}}
  .card-header.Low{{background:linear-gradient(90deg,#27ae60,#1e8449);color:#fff}}
  .card-name{{font-size:15px;font-weight:700}}
  .card-meta{{font-size:12px;opacity:.85;margin-top:2px}}
  .card-score{{font-size:26px;font-weight:800}}
  .card-body{{padding:16px 18px}}
  .data-badges{{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px}}
  .data-badge{{background:#f0f4fa;border-radius:6px;padding:5px 10px;font-size:11px;color:#2c3e50}}
  .data-badge strong{{color:#0070f3}}
  .data-badge.warn{{background:#fef3e2;color:#d68910}}
  .data-badge.warn strong{{color:#d68910}}
  .data-badge.danger{{background:#fde8e8;color:#c0392b}}
  .data-badge.danger strong{{color:#c0392b}}
  .data-badge.ok{{background:#e9f7ef;color:#1e8449}}
  .data-badge.ok strong{{color:#1e8449}}
  .factor-tags{{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:14px}}
  .factor-tag{{background:#2c3e50;color:#fff;border-radius:4px;padding:3px 9px;font-size:11px;font-weight:600}}
  .reasons{{list-style:none}}
  .reasons li{{padding:7px 0;border-top:1px solid #f0f0f0;font-size:13px;line-height:1.5;color:#555}}
  .reasons li::before{{content:attr(data-n)". ";font-weight:700;color:#0070f3}}
  .reasons li b{{color:#2c3e50}}
  .count-label{{font-size:13px;color:#666;margin-bottom:10px}}
</style>
</head>
<body>

<div class="header">
  <h1>Flight Attrition Risk Dashboard</h1>
  <p>SAP SuccessFactors SFSALES010044 + Payroll SFSALES009656 &nbsp;|&nbsp; August 2026 &nbsp;|&nbsp; 8-Factor Model</p>
</div>

<div class="tabs">
  <div class="tab active" onclick="showTab('overview',this)">Overview &amp; Charts</div>
  <div class="tab" onclick="showTab('register',this)">Employee Risk Register</div>
  <div class="tab" onclick="showTab('explanations',this)">High-Risk Explanations</div>
</div>

<div id="overview" class="panel active">
  <div class="kpi-row">
    <div class="kpi neu"><div class="kpi-val">{TOTAL}</div><div class="kpi-label">Active Employees</div></div>
    <div class="kpi high"><div class="kpi-val">{H}</div><div class="kpi-label">High Risk &nbsp;({pct(H,TOTAL)})</div></div>
    <div class="kpi med"><div class="kpi-val">{M}</div><div class="kpi-label">Medium Risk &nbsp;({pct(M,TOTAL)})</div></div>
    <div class="kpi low"><div class="kpi-val">{L}</div><div class="kpi-label">Low Risk &nbsp;({pct(L,TOTAL)})</div></div>
    <div class="kpi neu"><div class="kpi-val">{summary['avg_risk_score']}</div><div class="kpi-label">Avg Risk Score</div></div>
    <div class="kpi neu"><div class="kpi-val">{summary['avg_compa_ratio']:.2f}</div><div class="kpi-label">Avg Compa-Ratio</div></div>
    <div class="kpi high"><div class="kpi-val">{summary['below_market_employees']}</div><div class="kpi-label">Below Market Pay (&lt;0.9)</div></div>
    <div class="kpi neu"><div class="kpi-val">{summary['employees_with_absences']}</div><div class="kpi-label">With Absence Records</div></div>
  </div>

  <div class="charts-grid">
    <div class="chart-card">
      <h3>Risk Band Distribution</h3>
      <canvas id="donutChart"></canvas>
    </div>
    <div class="chart-card">
      <h3>Risk Score Distribution</h3>
      <canvas id="histChart"></canvas>
    </div>
    <div class="chart-card wide">
      <h3>Risk Factor Sub-Scores — All Employees vs High-Risk Cohort &nbsp;<small style="font-weight:400;color:#888">(weights shown in brackets)</small></h3>
      <canvas id="factorChart"></canvas>
    </div>
    <div class="chart-card">
      <h3>Average Risk Score by Department (Top 12)</h3>
      <canvas id="deptChart"></canvas>
    </div>
    <div class="chart-card">
      <h3>Factor Weights</h3>
      <canvas id="weightChart"></canvas>
    </div>
  </div>
</div>

<div id="register" class="panel">
  <div class="controls">
    <input type="text" id="searchBox" placeholder="Search name, department, location..." oninput="filterTable()">
    <select id="bandFilter" onchange="filterTable()">
      <option value="">All Risk Bands</option>
      <option value="High">High Risk</option>
      <option value="Medium">Medium Risk</option>
      <option value="Low">Low Risk</option>
    </select>
    <select id="sortSelect" onchange="sortTable()">
      <option value="riskScore_desc">Sort: Highest Risk First</option>
      <option value="riskScore_asc">Sort: Lowest Risk First</option>
      <option value="compaRatio_asc">Sort: Lowest Compa-Ratio First</option>
      <option value="tenure_desc">Sort: Longest Tenure First</option>
      <option value="name_asc">Sort: Name A–Z</option>
    </select>
    <select id="factorFilter" onchange="filterTable()">
      <option value="">Filter by Risk Factor</option>
      <option value="f_compa_ratio">Below Market Pay</option>
      <option value="f_role_stagnation">Role Stagnation (5+ yrs)</option>
      <option value="f_no_bonus">No Bonus History</option>
      <option value="f_stale_comp">Stale Compensation (24+ mths)</option>
      <option value="f_only_hire">No Raise Since Hire</option>
      <option value="f_high_absence">High Absence (5+ events)</option>
    </select>
  </div>
  <div class="count-label" id="tableCount"></div>
  <table id="empTable">
    <thead>
      <tr>
        <th onclick="sortBy('name')">Name</th>
        <th onclick="sortBy('dept')">Department</th>
        <th onclick="sortBy('location')">Location</th>
        <th onclick="sortBy('tenure')">Co. Tenure (yrs)</th>
        <th onclick="sortBy('roleTenure')">Role Tenure (yrs)</th>
        <th onclick="sortBy('perf')">Perf Rating</th>
        <th onclick="sortBy('mthsSinceRaise')">Mths Since Raise</th>
        <th onclick="sortBy('compaRatio')">Compa-Ratio</th>
        <th onclick="sortBy('salary')">Base Salary</th>
        <th onclick="sortBy('absenceCount')">Absences</th>
        <th onclick="sortBy('riskScore')">Risk Score</th>
        <th>Band</th>
      </tr>
    </thead>
    <tbody id="empBody"></tbody>
  </table>
</div>

<div id="explanations" class="panel">
  <div class="filters-row">
    <input type="text" id="cardSearch" placeholder="Search employee name or department..." oninput="filterCards()">
    <select id="cardSort" onchange="sortCards()">
      <option value="riskScore_desc">Sort: Highest Risk First</option>
      <option value="compaRatio_asc">Sort: Lowest Compa-Ratio First</option>
      <option value="name_asc">Sort: Name A–Z</option>
    </select>
    <select id="cardFactorFilter" onchange="filterCards()">
      <option value="">All Risk Drivers</option>
      <option value="Below Market Pay">Below Market Pay</option>
      <option value="Role Stagnation">Role Stagnation</option>
      <option value="Low Performance">Low Performance</option>
      <option value="No Bonus History">No Bonus History</option>
      <option value="High Absence">High Absence</option>
      <option value="Stale Compensation">Stale Compensation</option>
    </select>
  </div>
  <div class="count-label" id="cardCount"></div>
  <div class="cards-grid" id="cardsGrid"></div>
</div>

<script>
const EMPLOYEES = {json.dumps(emp_records)};
const HIGH_CARDS = {json.dumps(high_cards)};

// ── tabs ───────────────────────────────────────────────────────────────────
function showTab(id, el) {{
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  el.classList.add('active');
}}

// ── charts ─────────────────────────────────────────────────────────────────
const H='#e74c3c', M='#f39c12', Lo='#27ae60', B='#3498db', D='#2c3e50';

// Donut
new Chart(document.getElementById('donutChart'), {{
  type:'doughnut',
  data:{{
    labels:['High ({H})','Medium ({M})','Low ({L})'],
    datasets:[{{data:[{H},{M},{L}],backgroundColor:[H,M,Lo],borderWidth:3,hoverOffset:6}}]
  }},
  options:{{plugins:{{legend:{{position:'bottom'}},
    tooltip:{{callbacks:{{label:(c)=>` ${{c.label}}: ${{c.raw}} employees (${{Math.round(c.raw/{TOTAL}*100)}}%)`}}}}
  }},cutout:'55%'}}
}});

// Histogram
const histLabels = {json.dumps(labels)};
new Chart(document.getElementById('histChart'), {{
  type:'bar',
  data:{{
    labels:histLabels,
    datasets:[
      {{label:'High',data:{json.dumps(high_hist)},backgroundColor:H}},
      {{label:'Medium',data:{json.dumps(med_hist)},backgroundColor:M}},
      {{label:'Low',data:{json.dumps(low_hist)},backgroundColor:Lo}},
    ]
  }},
  options:{{scales:{{x:{{stacked:true,title:{{display:true,text:'Risk Score Range'}}}},y:{{stacked:true,title:{{display:true,text:'Employees'}}}}}},plugins:{{legend:{{position:'top'}}}}}}
}});

// Factor chart — computed from employee data
const WEIGHTS = {json.dumps(WEIGHTS)};
const FACTOR_LABELS = {json.dumps(FACTOR_LABELS)};
function computeFactorScores(emp) {{
  const cr = emp.compaRatio;
  return {{
    f_role_stagnation: Math.min((emp.roleTenure||0)/5*100,100),
    f_low_perf:        emp.perf==null?60:Math.max(0,Math.min((5-(emp.perf||3))/4*100,100)),
    f_compa_ratio:     cr==null?50:Math.max(0,Math.min((1.1-cr)/0.6*100,100)),
    f_stale_comp:      Math.min((emp.mthsSinceRaise||36)/36*100,100),
    f_only_hire:       emp.onlyHire?100:0,
    f_short_tenure:    Math.max(0,(2-Math.min(emp.tenure||0,2))/2*100),
    f_no_bonus:        emp.hasBonus?0:70,
    f_high_absence:    Math.min((emp.absenceCount||0)/10*100,100),
    f_mgr_instability: Math.min((emp.mgrChanges||0)/3*100,100),
  }};
}}
const allScores = EMPLOYEES.map(e=>computeFactorScores(e));
const highScores = EMPLOYEES.filter(e=>e.riskBand==='High').map(e=>computeFactorScores(e));
function avg(arr,key){{return arr.reduce((s,r)=>s+(r[key]||0),0)/arr.length;}}
const fKeys = Object.keys(WEIGHTS);
new Chart(document.getElementById('factorChart'), {{
  type:'bar',
  data:{{
    labels:fKeys.map(k=>{{const w=Math.round(WEIGHTS[k]*100);return FACTOR_LABELS[k]+' ['+w+'%]';}}),
    datasets:[
      {{label:'All Employees',data:fKeys.map(k=>Math.round(avg(allScores,k)*10)/10),backgroundColor:B+'cc'}},
      {{label:'High-Risk Cohort',data:fKeys.map(k=>Math.round(avg(highScores,k)*10)/10),backgroundColor:H+'cc'}},
    ]
  }},
  options:{{scales:{{y:{{max:105,title:{{display:true,text:'Sub-Score (0–100)'}}}}}},plugins:{{legend:{{position:'top'}}}}}}
}});

// Dept chart
const deptNames = {json.dumps(dept_risk.index.tolist())};
const deptScores = {json.dumps(dept_risk.values.tolist())};
const avgScore = {summary['avg_risk_score']};
new Chart(document.getElementById('deptChart'), {{
  type:'bar',
  data:{{
    labels:deptNames,
    datasets:[{{
      label:'Avg Risk Score',
      data:deptScores,
      backgroundColor:deptScores.map(v=>v>60?H:v>30?M:Lo)
    }}]
  }},
  options:{{indexAxis:'y',plugins:{{legend:{{display:false}},annotation:{{annotations:{{line1:{{type:'line',xMin:avgScore,xMax:avgScore,borderColor:'#666',borderDash:[4,4]}}}}}}}},scales:{{x:{{max:105}}}}}}
}});

// Weight pie
new Chart(document.getElementById('weightChart'), {{
  type:'doughnut',
  data:{{
    labels:fKeys.map(k=>FACTOR_LABELS[k]),
    datasets:[{{
      data:fKeys.map(k=>Math.round(WEIGHTS[k]*100)),
      backgroundColor:['#e74c3c','#f39c12','#9b59b6','#3498db','#1abc9c','#e67e22','#95a5a6','#2ecc71'],
      borderWidth:2
    }}]
  }},
  options:{{plugins:{{legend:{{position:'right',labels:{{font:{{size:11}}}}}},tooltip:{{callbacks:{{label:(c)=>` ${{c.label}}: ${{c.raw}}%`}}}}}}}}
}});

// ── register ───────────────────────────────────────────────────────────────
let tableData = [...EMPLOYEES];
let sortKey = 'riskScore', sortDir = -1;

function renderTable(data) {{
  const body = document.getElementById('empBody');
  document.getElementById('tableCount').textContent = `Showing ${{data.length}} of ${{EMPLOYEES.length}} employees`;
  body.innerHTML = data.map(e => {{
    const scoreColor = e.riskBand==='High'?'#e74c3c':e.riskBand==='Medium'?'#f39c12':'#27ae60';
    const crColor = e.compaRatio==null?'':e.compaRatio<0.9?'color:#e74c3c;font-weight:700':e.compaRatio>1.1?'color:#27ae60':'';
    const crVal = e.compaRatio!=null ? `<span style="${{crColor}}">${{e.compaRatio.toFixed(3)}}</span>` : '—';
    const sal = e.salary ? `${{e.currency}} ${{e.salary.toLocaleString()}}` : '—';
    const ab = e.absenceCount>0 ? `<span style="${{e.absenceCount>=5?'color:#e74c3c;font-weight:700':''}}">${{e.absenceCount}}</span>` : '0';
    return `<tr>
      <td><b>${{e.name}}</b></td>
      <td>${{e.dept}}</td>
      <td>${{e.location}}</td>
      <td>${{e.tenure!=null?e.tenure.toFixed(1):'—'}}</td>
      <td>${{e.roleTenure!=null?e.roleTenure.toFixed(1):'—'}}</td>
      <td>${{e.perf!=null?e.perf.toFixed(1)+'/5':'—'}}</td>
      <td>${{e.mthsSinceRaise!=null?Math.round(e.mthsSinceRaise)+' mths':'—'}}</td>
      <td>${{crVal}}</td>
      <td>${{sal}}</td>
      <td>${{ab}}</td>
      <td>
        <div class="score-bar-wrap">
          <div class="score-bar" style="width:${{e.riskScore}}px;max-width:80px;background:${{scoreColor}}"></div>
          <b style="color:${{scoreColor}}">${{e.riskScore.toFixed(1)}}</b>
        </div>
      </td>
      <td><span class="badge ${{e.riskBand}}">${{e.riskBand}}</span></td>
    </tr>`;
  }}).join('');
}}

function filterTable() {{
  const q = document.getElementById('searchBox').value.toLowerCase();
  const band = document.getElementById('bandFilter').value;
  const ff = document.getElementById('factorFilter').value;
  let d = EMPLOYEES.filter(e => {{
    if (q && !e.name.toLowerCase().includes(q) && !e.dept.toLowerCase().includes(q) && !e.location.toLowerCase().includes(q)) return false;
    if (band && e.riskBand !== band) return false;
    if (ff) {{
      if (ff==='f_compa_ratio' && (e.compaRatio==null||e.compaRatio>=0.9)) return false;
      if (ff==='f_role_stagnation' && (e.roleTenure==null||e.roleTenure<5)) return false;
      if (ff==='f_no_bonus' && e.hasBonus) return false;
      if (ff==='f_stale_comp' && (e.mthsSinceRaise==null||e.mthsSinceRaise<24)) return false;
      if (ff==='f_only_hire' && !e.onlyHire) return false;
      if (ff==='f_high_absence' && e.absenceCount<5) return false;
    }}
    return true;
  }});
  renderTable(sortData(d));
}}

function sortData(d) {{
  return [...d].sort((a,b) => {{
    const av = a[sortKey], bv = b[sortKey];
    if (av==null && bv==null) return 0;
    if (av==null) return 1; if (bv==null) return -1;
    if (typeof av==='string') return av.localeCompare(bv)*sortDir;
    return (av-bv)*sortDir;
  }});
}}

function sortBy(key) {{
  if (sortKey===key) sortDir=-sortDir; else {{sortKey=key;sortDir=-1;}}
  filterTable();
}}

function sortTable() {{
  const val = document.getElementById('sortSelect').value;
  const [k,d] = val.split('_');
  sortKey=k; sortDir=d==='desc'?-1:1;
  filterTable();
}}

filterTable();

// ── cards ──────────────────────────────────────────────────────────────────
let cardsData = [...HIGH_CARDS];

function badgeClass(key, val) {{
  if (key==='compaRatio') return val!=null&&val<0.9?'danger':val!=null&&val>1.1?'ok':'';
  if (key==='absence') return val>=5?'danger':val>0?'warn':'ok';
  if (key==='perf') return val!=null&&val<=2?'danger':val!=null&&val>=4?'ok':'';
  if (key==='bonus') return val?'ok':'warn';
  return '';
}}

function renderCards(data) {{
  document.getElementById('cardCount').textContent = `Showing ${{data.length}} high-risk employees`;
  document.getElementById('cardsGrid').innerHTML = data.map(c => {{
    const sal = c.salary ? `${{c.currency}} ${{c.salary.toLocaleString()}}` : '—';
    const cr  = c.compaRatio!=null ? c.compaRatio.toFixed(3) : '—';
    const badges = [
      {{label:'Role Tenure',val:c.roleTenure!=null?c.roleTenure.toFixed(1)+' yrs':'—',cls:''}},
      {{label:'Co. Tenure',val:c.tenure!=null?c.tenure.toFixed(1)+' yrs':'—',cls:''}},
      {{label:'Perf Rating',val:c.perf!=null?c.perf.toFixed(1)+'/5':'No Review',cls:badgeClass('perf',c.perf)}},
      {{label:'Last Raise',val:c.mthsRaise!=null?Math.round(c.mthsRaise)+' mths ago':'—',cls:c.mthsRaise!=null&&c.mthsRaise>24?'warn':''}},
      {{label:'Salary',val:sal,cls:''}},
      {{label:'Compa-Ratio',val:cr,cls:badgeClass('compaRatio',c.compaRatio)}},
      {{label:'Bonus',val:c.hasBonus?'Yes':'None',cls:badgeClass('bonus',c.hasBonus)}},
      {{label:'Absences',val:c.absenceCount+' events',cls:badgeClass('absence',c.absenceCount)}},
    ].map(b=>`<div class="data-badge ${{b.cls}}"><strong>${{b.label}}:</strong> ${{b.val}}</div>`).join('');
    const factors = c.topFactors.map(f=>`<span class="factor-tag">${{f.label}}</span>`).join('');
    const reasons = [c.explanation_1,c.explanation_2,c.explanation_3].map((r,i)=>
      `<li data-n="${{i+1}}">${{r.replace(/\\*\\*(.*?)\\*\\*/g,'<b>$1</b>')}}</li>`
    ).join('');
    return `<div class="card" data-name="${{c.name.toLowerCase()}}" data-dept="${{c.dept.toLowerCase()}}" data-factors="${{c.topFactors.map(f=>f.label).join('|')}}">
      <div class="card-header ${{c.riskBand}}">
        <div>
          <div class="card-name">#${{c.rank}} — ${{c.name}}</div>
          <div class="card-meta">${{c.dept}}</div>
        </div>
        <div class="card-score">${{c.riskScore}}</div>
      </div>
      <div class="card-body">
        <div class="data-badges">${{badges}}</div>
        <div class="factor-tags">${{factors}}</div>
        <ol class="reasons">${{reasons}}</ol>
      </div>
    </div>`;
  }}).join('');
}}

function filterCards() {{
  const q = document.getElementById('cardSearch').value.toLowerCase();
  const ff = document.getElementById('cardFactorFilter').value;
  let d = HIGH_CARDS.filter(c => {{
    if (q && !c.name.toLowerCase().includes(q) && !c.dept.toLowerCase().includes(q)) return false;
    if (ff && !c.topFactors.some(f=>f.label.includes(ff))) return false;
    return true;
  }});
  renderCards(sortCardsData(d));
}}

function sortCardsData(d) {{
  const val = document.getElementById('cardSort').value;
  const [k,dir] = val.split('_');
  return [...d].sort((a,b) => {{
    if (k==='riskScore') return dir==='desc'?b.riskScore-a.riskScore:a.riskScore-b.riskScore;
    if (k==='compaRatio') {{
      const av=a.compaRatio??999, bv=b.compaRatio??999;
      return dir==='asc'?av-bv:bv-av;
    }}
    if (k==='name') return dir==='asc'?a.name.localeCompare(b.name):b.name.localeCompare(a.name);
    return 0;
  }});
}}

function sortCards() {{ filterCards(); }}

renderCards(cardsData);
</script>
</body>
</html>"""

out = FIXTURES / 'attrition_dashboard.html'
out.write_text(html, encoding='utf-8')
size_kb = out.stat().st_size // 1024
print(f"Dashboard written: {out} ({size_kb} KB)")
print(f"Employees: {TOTAL} | High: {H} | Medium: {M} | Low: {L}")
print(f"High-risk cards: {len(high_cards)}")
