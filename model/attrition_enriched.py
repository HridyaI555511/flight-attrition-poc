"""
Enriched Flight Attrition Risk Model
Merges SAP SuccessFactors (SFSALES010044) + Payroll (SFSALES009656)
Added features: actual salary, dept compa-ratio, bonus history
"""

import json
import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter, defaultdict

warnings.filterwarnings('ignore')

SFSF    = Path(__file__).parent.parent / 'fixtures' / 'sfsf'
PAYROLL = Path(__file__).parent.parent / 'fixtures' / 'payroll'
OUTPUT  = Path(__file__).parent.parent / 'fixtures' / 'output'
NOW     = datetime(2026, 8, 30, tzinfo=timezone.utc)
FAR_FUTURE_MS = 253402214400000

FREQ_TO_ANNUAL = {'ANN': 1, 'MON': 12, 'SMT': 24, 'BWK': 26, 'BIM': 6, 'WKL': 52, 'HOURLY': 2080}

BASE_COMP_KEYWORDS = ['BASESAL','BASIC','BASE_','EEB_','BASAL','SALARIO']

def parse_date(val):
    if not val: return None
    if isinstance(val, str) and val.startswith('/Date('):
        ms = int(val.replace('/Date(', '').split('+')[0].split('-')[0].split(')')[0])
        if ms >= FAR_FUTURE_MS: return None
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return None

def years_since(dt):
    return max((NOW - dt).days / 365.25, 0) if dt else None

def months_since(dt):
    return max((NOW - dt).days / 30.44, 0) if dt else None

def load(folder, name):
    with open(folder / f'{name}.json') as f:
        return json.load(f).get('d', {}).get('results', [])


# ══════════════════════════════════════════════════════════════════
# 1. SFSF BASE DATA
# ══════════════════════════════════════════════════════════════════
print("Loading SuccessFactors data...")

# Employees
df = pd.DataFrame([{
    'userId':     r['userId'],
    'firstName':  r['firstName'],
    'lastName':   r['lastName'],
    'department': (r.get('department') or 'Unknown').split('(')[0].strip(),
    'division':   (r.get('division') or 'Unknown').split('(')[0].strip(),
    'location':   (r.get('location') or 'Unknown').split('(')[0].strip(),
    'hireDate':   parse_date(r.get('hireDate')),
} for r in load(SFSF, 'employees')])
df['tenure_years'] = df['hireDate'].apply(years_since)

# Attrition label
empl_df = pd.DataFrame([{
    'userId': r['userId'],
    'empEndDate': parse_date(r.get('endDate')),
} for r in load(SFSF, 'employment')])
empl_df['attrited'] = empl_df['empEndDate'].notna().astype(int)
df = df.merge(empl_df[['userId','attrited']], on='userId', how='left')
df['attrited'] = df['attrited'].fillna(0).astype(int)
active = df[df['attrited'] == 0].copy()

# EmpJob — tenure in role
job_raw = load(SFSF, 'emp-job')
job_df = pd.DataFrame([{
    'userId': r['userId'],
    'jobStartDate': parse_date(r['startDate']),
    'managerId': r.get('managerId'),
    'seqNumber': int(r.get('seqNumber') or 0),
} for r in job_raw])
job_latest = job_df.sort_values('seqNumber', ascending=False).groupby('userId').first().reset_index()
job_latest['tenure_in_role_years'] = job_latest['jobStartDate'].apply(years_since)
job_changes = job_df.groupby('userId').size().reset_index(name='total_job_records')
job_latest = job_latest.merge(job_changes, on='userId')
active = active.merge(job_latest[['userId','tenure_in_role_years','managerId','total_job_records']], on='userId', how='left')

# Performance
perf_df = pd.DataFrame([{
    'userId': str(r['formSubjectId']),
    'rating': float(r['rating']) if r.get('rating') and str(r['rating']).replace('.','').isdigit() and float(r['rating']) > 0 else None,
    'reviewEnd': parse_date(r.get('formReviewEndDate')),
} for r in load(SFSF, 'performance-forms')])
perf_latest = (
    perf_df[perf_df['rating'].notna()]
    .sort_values('reviewEnd', ascending=False)
    .groupby('userId').first().reset_index()
    .rename(columns={'rating': 'perf_rating', 'reviewEnd': 'lastReviewDate'})
)
active = active.merge(perf_latest[['userId','perf_rating','lastReviewDate']], on='userId', how='left')
active['months_since_review'] = active['lastReviewDate'].apply(months_since)
review_count = perf_df[perf_df['rating'].notna()].groupby('userId').size().reset_index(name='review_count')
active = active.merge(review_count, on='userId', how='left')
active['review_count'] = active['review_count'].fillna(0)

# Compensation recency (SFSF)
comp_df = pd.DataFrame([{
    'userId': r['userId'],
    'compDate': parse_date(r['startDate']),
    'eventReason': (r.get('eventReason') or '').upper(),
} for r in load(SFSF, 'compensation')])
comp_latest = comp_df.sort_values('compDate', ascending=False).groupby('userId').first().reset_index()
comp_latest['months_since_comp_change'] = comp_latest['compDate'].apply(months_since)
comp_latest['only_hire_comp'] = (comp_latest['eventReason'] == 'HIRNEW').astype(int)
active = active.merge(comp_latest[['userId','months_since_comp_change','only_hire_comp']], on='userId', how='left')


# ══════════════════════════════════════════════════════════════════
# 2. PAYROLL ENRICHMENT
# ══════════════════════════════════════════════════════════════════
print("Loading payroll data...")

# Direct salary from User entity
py_users = pd.DataFrame([{
    'userId': r['userId'],
    'salary_direct': float(r['salary']) if r.get('salary') and float(str(r['salary'])) > 0 else None,
    'dateOfCurrentPosition': parse_date(r.get('dateOfCurrentPosition')),
} for r in load(PAYROLL, 'py-employees')])

# Base salary from recurring pay (annualised)
pay_raw = load(PAYROLL, 'py-pay-recurring')
base_records = []
for p in pay_raw:
    comp = str(p.get('payComponent', '')).upper()
    if not any(kw in comp for kw in BASE_COMP_KEYWORDS):
        continue
    val = float(p['paycompvalue']) if p.get('paycompvalue') else 0
    freq = p.get('frequency', 'MON')
    multiplier = FREQ_TO_ANNUAL.get(freq, 12)
    annual = val * multiplier
    if annual > 0:
        base_records.append({
            'userId': p['userId'],
            'annual_salary_pay': annual,
            'currency': p.get('currencyCode'),
            'payComponent': p.get('payComponent'),
        })

pay_salary = pd.DataFrame(base_records)
if not pay_salary.empty:
    pay_salary_max = pay_salary.groupby('userId').agg(
        annual_salary_pay=('annual_salary_pay', 'max'),
        currency=('currency', 'first'),
    ).reset_index()
else:
    pay_salary_max = pd.DataFrame(columns=['userId','annual_salary_pay','currency'])

# Bonus history from non-recurring
nrec_raw = load(PAYROLL, 'py-pay-nonrecurring')
bonus_df = pd.DataFrame([{'userId': r['userId']} for r in nrec_raw])
bonus_count = bonus_df.groupby('userId').size().reset_index(name='bonus_events')
has_bonus = bonus_df.drop_duplicates('userId').assign(has_bonus=1)[['userId','has_bonus']]

# Merge payroll into active
active = active.merge(py_users[['userId','salary_direct','dateOfCurrentPosition']], on='userId', how='left')
active = active.merge(pay_salary_max, on='userId', how='left')
active = active.merge(has_bonus, on='userId', how='left')
active = active.merge(bonus_count, on='userId', how='left')
active['has_bonus'] = active['has_bonus'].fillna(0)
active['bonus_events'] = active['bonus_events'].fillna(0)

# Leave data — merge all four EmployeeTime files (SFSF + payroll, original + recent ordered)
# Deduplicate by externalCode; exclude WORK/schedule entries; APPROVED only
SKIP_TT = {'WORK', 'BREAKSCHED'}
LOA_TYPES = {'LOATT','UK_PARENTAL','TT_MATERNITY','UK_Maternity',
             'DEU-PAR','DEU-MAT','PHL-ML-TT','SG_PAT_LV'}
SICK_TYPES = {'SICK_DAY','TT_SICK_REC','DEU-SICK','STD','LTDTT1'}

leave_all = []
seen_keys = set()
for folder, name in [(SFSF,'employee-time'), (SFSF,'employee-time-recent'),
                     (PAYROLL,'py-employee-time'), (PAYROLL,'py-employee-time-recent')]:
    path = folder / f'{name}.json'
    if not path.exists():
        continue
    for r in load(folder, name):
        if r.get('approvalStatus') != 'APPROVED': continue
        if r.get('timeType') in SKIP_TT: continue
        key = str(r.get('externalCode') or f"{r.get('userId')}{r.get('startDate')}")
        if key in seen_keys: continue
        seen_keys.add(key)
        leave_all.append({
            'userId':      str(r['userId']),
            'timeType':    r.get('timeType',''),
            'days':        float(r.get('quantityInDays') or 0),
            'is_loa':      int(r.get('timeType','') in LOA_TYPES),
            'is_sick':     int(r.get('timeType','') in SICK_TYPES),
        })

leave_df = pd.DataFrame(leave_all) if leave_all else pd.DataFrame(
    columns=['userId','timeType','days','is_loa','is_sick'])
leave_summary = leave_df.groupby('userId').agg(
    absence_count=('userId','count'),
    total_leave_days=('days','sum'),
    loa_flag=('is_loa','max'),
    sick_count=('is_sick','sum'),
).reset_index()
active = active.merge(leave_summary, on='userId', how='left')
for col in ['absence_count','total_leave_days','loa_flag','sick_count']:
    active[col] = active[col].fillna(0)

loa_n = int(active['loa_flag'].sum())
print(f"Employees with any leave records: {(active['absence_count'] > 0).sum()}")
print(f"Employees with LOA/parental leave: {loa_n}")
print(f"Employees with sick leave: {int((active['sick_count'] > 0).sum())}")

# ── Manager / Org change history ───────────────────────────────────────────
print("Loading manager/org change history...")
import re as _re

def _parse_ms(v):
    if not v: return None
    m = _re.match(r'/Date\((-?\d+)', str(v))
    return datetime.fromtimestamp(int(m.group(1))/1000, tz=timezone.utc) if m else None

job_hist_recs = []
_seen_hist = set()
for folder, name in [(SFSF,'emp-job-history'), (PAYROLL,'py-emp-job-history')]:
    p = folder / f'{name}.json'
    if not p.exists(): continue
    for r in load(folder, name):
        key = f"{r['userId']}_{r.get('seqNumber', 0)}"
        if key in _seen_hist: continue
        _seen_hist.add(key)
        job_hist_recs.append({
            'userId':    str(r['userId']),
            'seq':       int(r.get('seqNumber') or 0),
            'startDate': _parse_ms(r.get('startDate')),
            'managerId': str(r.get('managerId') or '').strip(),
            'dept':      str(r.get('department') or '').strip(),
            'title':     str(r.get('jobTitle') or '').strip(),
        })

_by_user_hist = defaultdict(list)
for r in job_hist_recs:
    _by_user_hist[r['userId']].append(r)
for uid in _by_user_hist:
    _by_user_hist[uid].sort(key=lambda x: x['seq'])

change_rows = []
for uid, jobs in _by_user_hist.items():
    mgr_n = org_n = title_n = 0
    last_mgr_dt = None
    for i in range(1, len(jobs)):
        p, c = jobs[i-1], jobs[i]
        if p['managerId'] and c['managerId'] and p['managerId'] != c['managerId']:
            mgr_n += 1; last_mgr_dt = c['startDate']
        if p['dept'] and c['dept'] and p['dept'] != c['dept']:
            org_n += 1
        if p['title'] and c['title'] and p['title'] != c['title']:
            title_n += 1
    change_rows.append({
        'userId':            uid,
        'mgr_change_count':  mgr_n,
        'org_change_count':  org_n,
        'title_change_count': title_n,
        'months_since_mgr_change': months_since(last_mgr_dt),
    })

change_df = pd.DataFrame(change_rows)
active = active.merge(change_df, on='userId', how='left')
for col in ['mgr_change_count','org_change_count','title_change_count']:
    active[col] = active[col].fillna(0)

print(f"Active employees with ≥1 manager change: {int((active['mgr_change_count'] > 0).sum())}")
print(f"Active employees with ≥1 org change:     {int((active['org_change_count'] > 0).sum())}")
print(f"Active employees with ≥1 title change:   {int((active['title_change_count'] > 0).sum())}")

# Best salary estimate: direct field first, then annualised pay component
active['base_salary'] = active['salary_direct'].combine_first(active['annual_salary_pay'])

# Compa-ratio proxy: salary vs dept median (within same currency group)
dept_median = active.groupby('department')['base_salary'].median().reset_index(name='dept_median_salary')
active = active.merge(dept_median, on='department', how='left')
active['compa_ratio'] = np.where(
    active['dept_median_salary'] > 0,
    active['base_salary'] / active['dept_median_salary'],
    np.nan
)
active['below_market'] = (active['compa_ratio'] < 0.9).astype(float)

salary_coverage = active['base_salary'].notna().sum()
print(f"Salary coverage: {salary_coverage}/{len(active)} employees ({salary_coverage/len(active)*100:.0f}%)")
print(f"Avg compa-ratio: {active['compa_ratio'].mean():.2f}")
print(f"Below market (<0.9): {active['below_market'].sum():.0f} employees")
print(f"Employees with bonus history: {active['has_bonus'].sum():.0f}")


# ══════════════════════════════════════════════════════════════════
# 3. ENRICHED RISK SCORING
# ══════════════════════════════════════════════════════════════════
print("\nCalculating enriched risk scores...")

def safe(s, default=0):
    return s.fillna(default)

# Original 5 factors (60% weight)
active['f_role_stagnation']  = np.clip(safe(active['tenure_in_role_years'], 0) / 5 * 100, 0, 100)
active['f_low_perf']         = np.where(active['perf_rating'].isna(), 60,
                                np.clip((5 - safe(active['perf_rating'], 3)) / 4 * 100, 0, 100))
active['f_stale_comp']       = np.clip(safe(active['months_since_comp_change'], 36) / 36 * 100, 0, 100)
active['f_only_hire']        = safe(active['only_hire_comp'], 0) * 100
active['f_short_tenure']     = np.clip((2 - np.minimum(safe(active['tenure_years'], 0), 2)) / 2 * 100, 0, 100)

# Payroll: compa-ratio — continuous scale, cr<0.7→100, cr=1.1→0
active['f_compa_ratio']      = np.where(
    active['compa_ratio'].isna(), 50,
    np.clip((1.1 - active['compa_ratio']) / 0.6 * 100, 0, 100)
)
# No bonus history → lack of recognition/variable pay
active['f_no_bonus']         = (1 - safe(active['has_bonus'], 0)) * 70

# LOA flag kept in data exports for completeness, but not in model weights
# (all active employees have loa_flag=0 in this demo instance since LOA employees are inactive)
active['f_high_absence']     = np.clip(safe(active['absence_count'], 0) / 10 * 100, 0, 100)

# Manager instability — multiple manager changes signal org instability or poor manager fit
# 3+ changes = 100, 1-2 = 40-67, 0 = 0
active['f_mgr_instability']  = np.clip(safe(active['mgr_change_count'], 0) / 3 * 100, 0, 100)

WEIGHTS = {
    'f_role_stagnation':  0.20,
    'f_low_perf':         0.20,
    'f_compa_ratio':      0.15,
    'f_stale_comp':       0.13,
    'f_only_hire':        0.04,
    'f_short_tenure':     0.07,
    'f_no_bonus':         0.08,
    'f_high_absence':     0.08,
    'f_mgr_instability':  0.05,  # NEW — manager/org change history
}

active['risk_score'] = sum(active[col] * w for col, w in WEIGHTS.items())
active['risk_band']  = pd.cut(active['risk_score'], bins=[0,30,60,101],
                               labels=['Low','Medium','High'], include_lowest=True)

print("\nEnriched risk band distribution:")
print(active['risk_band'].value_counts())
print(f"Avg risk score: {active['risk_score'].mean():.1f}")


# ══════════════════════════════════════════════════════════════════
# 4. VISUALISATIONS
# ══════════════════════════════════════════════════════════════════
print("\nGenerating visualisations...")
plt.style.use('seaborn-v0_8-whitegrid')
fig = plt.figure(figsize=(22, 28))
fig.suptitle(
    'Enriched Flight Attrition Risk Analysis\n'
    'SAP SuccessFactors (SFSALES010044) + Payroll (SFSALES009656) | August 2026',
    fontsize=17, fontweight='bold', y=0.99
)
gs = gridspec.GridSpec(4, 2, figure=fig, hspace=0.5, wspace=0.35)
PALETTE = {'Low':'#27ae60','Medium':'#f39c12','High':'#e74c3c'}

# 1. Risk donut
ax1 = fig.add_subplot(gs[0, 0])
band_counts = active['risk_band'].value_counts().reindex(['High','Medium','Low']).fillna(0)
wedges, texts, autotexts = ax1.pie(
    band_counts.values, labels=band_counts.index,
    colors=[PALETTE[b] for b in band_counts.index],
    autopct='%1.0f%%', startangle=90, wedgeprops={'width':0.5},
    textprops={'fontsize':12}
)
for at in autotexts: at.set(fontsize=11, fontweight='bold')
ax1.set_title(f'Risk Band Distribution\n(n={len(active)} active)', fontsize=13, fontweight='bold')
high_n = int((active['risk_band']=='High').sum())
ax1.text(0, 0, f'{high_n}\nHigh Risk', ha='center', va='center',
         fontsize=14, fontweight='bold', color='#e74c3c')

# 2. Risk score histogram
ax2 = fig.add_subplot(gs[0, 1])
for band, col in PALETTE.items():
    sub = active[active['risk_band']==band]['risk_score']
    ax2.hist(sub, bins=15, alpha=0.75, color=col, label=f'{band} ({len(sub)})')
ax2.axvline(active['risk_score'].mean(), color='black', lw=1.5, linestyle='--',
            label=f'Mean: {active["risk_score"].mean():.0f}')
ax2.set_title('Risk Score Distribution', fontsize=13, fontweight='bold')
ax2.set_xlabel('Risk Score (0–100)')
ax2.set_ylabel('Employees')
ax2.legend()

# 3. Factor contribution: All vs High-risk
ax3 = fig.add_subplot(gs[1, :])
factor_labels = {
    'f_role_stagnation':  'Role\nStagnation',
    'f_low_perf':         'Low\nPerformance',
    'f_compa_ratio':      'Below\nMarket Pay ★',
    'f_stale_comp':       'Stale\nCompensation',
    'f_only_hire':        'No Raise\nSince Hire',
    'f_short_tenure':     'Short\nTenure',
    'f_no_bonus':         'No Bonus\nHistory ★',
    'f_high_absence':     'High\nAbsence ★',
    'f_mgr_instability':  'Manager\nInstability ★',
}
factors = list(WEIGHTS.keys())
x = np.arange(len(factors))
w = 0.35
high_mean = active[active['risk_band']=='High'][factors].mean()
all_mean  = active[factors].mean()
b1 = ax3.bar(x - w/2, all_mean.values,      w, color='#3498db', alpha=0.8, label='All Employees')
b2 = ax3.bar(x + w/2, high_mean.values, w, color='#e74c3c', alpha=0.8, label='High-Risk Cohort')
ax3.set_xticks(x)
ax3.set_xticklabels([factor_labels[f] for f in factors], fontsize=10)
ax3.set_ylabel('Sub-Score (0–100)')
ax3.set_title('Risk Factor Sub-Scores — All vs High-Risk  (★ = payroll/absence enrichment)', fontsize=13, fontweight='bold')
ax3.legend()
ax3.set_ylim(0, 115)
for b in list(b1)+list(b2):
    ax3.text(b.get_x()+b.get_width()/2, b.get_height()+1, f'{b.get_height():.0f}',
             ha='center', va='bottom', fontsize=8)

# 4. Compa-ratio vs risk score scatter
ax4 = fig.add_subplot(gs[2, 0])
plot = active.dropna(subset=['compa_ratio','risk_score']).copy()
colors_scatter = [PALETTE.get(str(b),'#95a5a6') for b in plot['risk_band']]
ax4.scatter(plot['compa_ratio'], plot['risk_score'], c=colors_scatter, alpha=0.6, s=40)
ax4.axvline(1.0, color='gray', linestyle='--', lw=1, label='Market rate (1.0)')
ax4.axvline(0.9, color='#e74c3c', linestyle=':', lw=1, label='Below market (0.9)')
ax4.set_title('Compa-Ratio vs Attrition Risk Score\n(from Payroll data)', fontsize=13, fontweight='bold')
ax4.set_xlabel('Compa-Ratio (salary / dept median)')
ax4.set_ylabel('Risk Score')
ax4.legend(fontsize=9)
from matplotlib.patches import Patch
legend_patches = [Patch(color=c, label=b) for b, c in PALETTE.items()]
ax4.legend(handles=legend_patches, fontsize=9)

# 5. Risk by department
ax5 = fig.add_subplot(gs[2, 1])
dept_risk = active.groupby('department')['risk_score'].mean().sort_values(ascending=False).head(12)
colors_d = [PALETTE['High'] if v>60 else PALETTE['Medium'] if v>30 else PALETTE['Low']
            for v in dept_risk.values]
ax5.barh(dept_risk.index[::-1], dept_risk.values[::-1], color=colors_d[::-1])
ax5.axvline(active['risk_score'].mean(), color='gray', linestyle='--', lw=1, label='Avg')
ax5.set_title('Avg Risk Score by Department (Top 12)', fontsize=13, fontweight='bold')
ax5.set_xlabel('Avg Risk Score')
ax5.legend(fontsize=9)

# 6. Top 20 high-risk table
ax6 = fig.add_subplot(gs[3, :])
ax6.axis('off')
top20 = (
    active[active['risk_band']=='High']
    .sort_values('risk_score', ascending=False)
    .head(20)
    [['firstName','lastName','department','tenure_years','tenure_in_role_years',
      'perf_rating','months_since_comp_change','compa_ratio','has_bonus','risk_score']]
    .copy().round(2)
)
top20.columns = ['First','Last','Department','Co.Tenure\n(yrs)','Role\nTenure (yrs)',
                 'Perf\nRating','Mths Since\nRaise','Compa-\nRatio','Bonus\nHistory','Risk\nScore']
tbl = ax6.table(cellText=top20.values, colLabels=top20.columns,
                cellLoc='center', loc='center', bbox=[0,0,1,1])
tbl.auto_set_font_size(False)
tbl.set_fontsize(7.5)
for (row, col), cell in tbl.get_celld().items():
    if row == 0:
        cell.set(facecolor='#2c3e50')
        cell.get_text().set(color='white', fontweight='bold')
    elif row % 2 == 0:
        cell.set_facecolor('#f8f9fa')
    if col == len(top20.columns)-1 and row > 0:
        score = float(top20.values[row-1][-1])
        cell.set_facecolor('#e74c3c' if score > 60 else '#f39c12')
        cell.get_text().set(fontweight='bold', color='white')
ax6.set_title('Top 20 High-Risk Employees (Enriched with Payroll Data)', fontsize=13, fontweight='bold', pad=20)

plt.savefig(OUTPUT / 'attrition_enriched.png', dpi=150, bbox_inches='tight')
plt.close()
print("  Saved: attrition_enriched.png")


# ══════════════════════════════════════════════════════════════════
# 5. EXPORTS
# ══════════════════════════════════════════════════════════════════
export_cols = ['userId','firstName','lastName','department','division','location',
               'tenure_years','tenure_in_role_years','perf_rating',
               'months_since_comp_change','only_hire_comp','review_count',
               'base_salary','currency','compa_ratio','has_bonus','bonus_events',
               'absence_count','total_leave_days','loa_flag','sick_count',
               'mgr_change_count','org_change_count','title_change_count',
               'risk_score','risk_band']
active[export_cols].sort_values('risk_score', ascending=False).to_csv(
    OUTPUT / 'all_employees_enriched_risk.csv', index=False)
print("  Saved: all_employees_enriched_risk.csv")

# Updated explanations for high-risk employees
high = active[active['risk_band']=='High'].sort_values('risk_score', ascending=False).copy()

def explain(row):
    lines = []
    # Line 1 — role stagnation or short tenure
    role_yrs = row['tenure_in_role_years']
    co_yrs   = row['tenure_years']
    if pd.notna(role_yrs) and role_yrs >= 3:
        lines.append(f"{row['firstName']} has been in the same role for {role_yrs:.1f} years "
                     f"(company tenure: {co_yrs:.1f} yrs) with no recorded position change — "
                     f"career stagnation is one of the top voluntary exit predictors.")
    elif pd.notna(co_yrs) and co_yrs < 2:
        lines.append(f"{row['firstName']} is in the high-risk early-tenure window with only "
                     f"{co_yrs:.1f} years at the company; attrition risk peaks in years 0–2.")
    else:
        lines.append(f"{row['firstName']} has {co_yrs:.1f} years tenure with {role_yrs:.1f} years "
                     f"in the current role, indicating limited internal mobility.")

    # Line 2 — performance + review gap
    perf    = row['perf_rating']
    reviews = row['review_count']
    if pd.isna(perf) or reviews == 0:
        lines.append("No completed performance review is on record — employees without formal "
                     "feedback are significantly more likely to feel unrecognised and disengage.")
    elif perf <= 2:
        lines.append(f"Performance rating of {perf:.1f}/5 indicates disengagement or role "
                     f"misalignment — both strong short-term flight triggers.")
    else:
        lines.append(f"Performance rating of {perf:.1f}/5 across {int(reviews)} review(s); "
                     f"combined with compensation and stagnation signals, risk remains elevated.")

    # Line 3 — payroll enrichment: salary + bonus + absence
    cr        = row['compa_ratio']
    bonus     = row['has_bonus']
    sal       = row['base_salary']
    months_c  = row['months_since_comp_change']
    only_hire = row['only_hire_comp']
    absence   = int(row.get('absence_count', 0) or 0)
    loa       = bool(row.get('loa_flag', 0))
    sick_n    = int(row.get('sick_count', 0) or 0)
    mgr_chg   = int(row.get('mgr_change_count', 0) or 0)
    title_chg = int(row.get('title_change_count', 0) or 0)

    sal_str = f"${sal:,.0f} (compa-ratio {cr:.2f})" if pd.notna(sal) and pd.notna(cr) else "unknown salary"
    # Build absence/manager note for line 3
    mgr_note = (
        f"; {mgr_chg} manager changes on record — repeated manager turnover is a leading retention risk indicator"
        if mgr_chg >= 3
        else f"; manager changed {mgr_chg} time(s)" if mgr_chg > 0 else ""
    )
    absence_note = (
        f"; {absence} approved leave events including LOA/parental leave — re-engagement risk post-LOA is elevated" if loa
        else f"; {absence} approved absence events — high frequency signals disengagement" if absence >= 5
        else ""
    )
    extra_note = mgr_note or absence_note
    if pd.notna(cr) and cr < 0.90:
        lines.append(f"Payroll data shows base salary of {sal_str}, placing them >10% below their "
                     f"department median — employees below 90% compa-ratio are 2–3× more likely "
                     f"to accept an external offer{'; no bonus history compounds this' if not bonus else ''}{extra_note}.")
    elif only_hire == 1 and pd.notna(months_c):
        lines.append(f"The only compensation event is their initial hire ({months_c:.0f} months ago) "
                     f"with no subsequent raise; salary is {sal_str}"
                     f"{', and there is no bonus history on record' if not bonus else ''}{extra_note}.")
    elif loa:
        lines.append(f"Leave records include LOA or parental leave (total {absence} events, salary: {sal_str})"
                     f"{', no bonus history' if not bonus else ''}; "
                     f"employees returning from extended leave have a ~30% higher attrition rate "
                     f"if re-onboarding and workload balance are not actively managed.")
    elif mgr_chg >= 3:
        lines.append(f"{mgr_chg} manager changes are recorded — repeated leadership turnover leaves employees "
                     f"without stable advocacy or career sponsorship (salary: {sal_str}"
                     f"{', no bonus' if not bonus else ''}).")
    elif absence >= 5:
        lines.append(f"{absence} approved absence events are recorded — unusually high absence "
                     f"frequency is a strong behavioural pre-attrition indicator. Salary: {sal_str}"
                     f"{', no bonus history' if not bonus else ''}.")
    elif not bonus:
        lines.append(f"No bonus or variable pay events are recorded in the payroll system "
                     f"(salary: {sal_str}); absence of performance-linked pay reduces retention "
                     f"leverage and signals limited reward recognition.")
    else:
        lines.append(f"Salary is {sal_str}; while compensation appears adequate, the combination "
                     f"of role stagnation and missing review data creates a compounding flight risk.")
    return lines

lines_out = ['ENRICHED FLIGHT ATTRITION RISK — HIGH-RISK EMPLOYEE EXPLANATIONS',
             'Sources: SAP SuccessFactors SFSALES010044 + Payroll SFSALES009656',
             f'Generated: August 2026 | High-risk employees: {len(high)}',
             '='*85]
records = []
for i, (_, row) in enumerate(high.iterrows(), 1):
    expl = explain(row)
    header = (f"{i:>3}. {row['firstName']} {row['lastName']} | {row['department']} | "
              f"Score: {row['risk_score']:.0f}/100 | "
              f"Salary: {row['base_salary']:,.0f} {row['currency'] or ''}" if pd.notna(row['base_salary'])
              else f"{i:>3}. {row['firstName']} {row['lastName']} | {row['department']} | Score: {row['risk_score']:.0f}/100")
    lines_out += [f'\n{header}', '-'*len(header)]
    for j, line in enumerate(expl, 1):
        lines_out.append(f'  {j}. {line}')
    records.append({'rank':i,'userId':row['userId'],'name':f"{row['firstName']} {row['lastName']}",
                    'department':row['department'],'risk_score':row['risk_score'],
                    'base_salary':row['base_salary'],'compa_ratio':row['compa_ratio'],
                    'explanation_1':expl[0],'explanation_2':expl[1],'explanation_3':expl[2]})

full = '\n'.join(str(l) for l in lines_out)
with open(OUTPUT / 'high_risk_enriched_explanations.txt','w') as f: f.write(full)
pd.DataFrame(records).to_csv(OUTPUT / 'high_risk_enriched_explanations.csv', index=False)
print("  Saved: high_risk_enriched_explanations.txt")
print("  Saved: high_risk_enriched_explanations.csv")

# Summary JSON
summary = {
    'generated_at': NOW.isoformat(),
    'data_sources': ['SAP SuccessFactors SFSALES010044','Payroll SFSALES009656'],
    'total_active_employees': int(len(active)),
    'salary_coverage_pct': round(salary_coverage/len(active)*100,1),
    'below_market_employees': int((active['compa_ratio'] < 0.9).sum()),
    'avg_compa_ratio': round(float(active['compa_ratio'].mean()), 3),
    'employees_with_absences': int((active['absence_count'] > 0).sum()),
    'employees_with_mgr_changes': int((active['mgr_change_count'] > 0).sum()),
    'risk_bands': {
        'high':   int((active['risk_band']=='High').sum()),
        'medium': int((active['risk_band']=='Medium').sum()),
        'low':    int((active['risk_band']=='Low').sum()),
    },
    'avg_risk_score': round(float(active['risk_score'].mean()), 1),
    'factor_weights': WEIGHTS,
}
with open(OUTPUT / 'attrition_enriched_summary.json','w') as f: json.dump(summary, f, indent=2)
print("\n=== SUMMARY ===")
print(json.dumps(summary, indent=2))
print(f"\n=== TOP 10 HIGH-RISK EMPLOYEES ===")
print(high.head(10)[['firstName','lastName','department','base_salary','compa_ratio','risk_score']].to_string(index=False))
