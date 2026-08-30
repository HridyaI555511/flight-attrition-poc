"""
Flight Attrition Risk Scoring — SAP SuccessFactors POC
Uses weighted rule-based scoring since the demo instance has very few labelled
attritions. This is the industry-standard approach for HR POCs.
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

warnings.filterwarnings('ignore')

FIXTURES = Path(__file__).parent.parent / 'fixtures' / 'sfsf'
OUTPUT   = FIXTURES
NOW      = datetime(2026, 8, 30, tzinfo=timezone.utc)
FAR_FUTURE_MS = 253402214400000

# ── helpers ───────────────────────────────────────────────────────────────────

def parse_date(val):
    if not val:
        return None
    if isinstance(val, str) and val.startswith('/Date('):
        ms = int(val.replace('/Date(', '').split('+')[0].split('-')[0].split(')')[0])
        if ms >= FAR_FUTURE_MS:
            return None
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return None

def years_since(dt):
    if dt is None:
        return None
    return max((NOW - dt).days / 365.25, 0)

def months_since(dt):
    if dt is None:
        return None
    return max((NOW - dt).days / 30.44, 0)

def load(name):
    with open(FIXTURES / f'{name}.json') as f:
        raw = json.load(f)
    return raw.get('d', {}).get('results', [])


# ── 1. Load ───────────────────────────────────────────────────────────────────

print("Loading data...")
emp_raw  = load('employees')
job_raw  = load('emp-job')
comp_raw = load('compensation')
perf_raw = load('performance-forms')
empl_raw = load('employment')
pay_raw  = load('pay-compensation')


# ── 2. Base employee frame ────────────────────────────────────────────────────

df = pd.DataFrame([{
    'userId':     r['userId'],
    'firstName':  r['firstName'],
    'lastName':   r['lastName'],
    'department': (r.get('department') or 'Unknown').split('(')[0].strip(),
    'division':   (r.get('division') or 'Unknown').split('(')[0].strip(),
    'location':   (r.get('location') or 'Unknown').split('(')[0].strip(),
    'hireDate':   parse_date(r.get('hireDate')),
} for r in emp_raw])

df['tenure_years'] = df['hireDate'].apply(years_since)


# ── 3. Employment status (active only) ───────────────────────────────────────

empl_df = pd.DataFrame([{
    'userId':     r['userId'],
    'empEndDate': parse_date(r.get('endDate')),
} for r in empl_raw])

empl_df['attrited'] = empl_df['empEndDate'].notna().astype(int)
df = df.merge(empl_df[['userId','attrited']], on='userId', how='left')
df['attrited'] = df['attrited'].fillna(0).astype(int)

# Work only with active employees for risk scoring
active = df[df['attrited'] == 0].copy()
print(f"Active employees to score: {len(active)}")


# ── 4. Job tenure & manager ───────────────────────────────────────────────────

job_df = pd.DataFrame([{
    'userId':      r['userId'],
    'jobStartDate': parse_date(r['startDate']),
    'managerId':   r.get('managerId'),
    'seqNumber':   int(r.get('seqNumber') or 0),
} for r in job_raw])

job_latest = job_df.sort_values('seqNumber', ascending=False).groupby('userId').first().reset_index()
job_latest['tenure_in_role_years'] = job_latest['jobStartDate'].apply(years_since)

job_changes = job_df.groupby('userId').size().reset_index(name='total_job_records')
job_latest = job_latest.merge(job_changes, on='userId')

active = active.merge(job_latest[['userId','tenure_in_role_years','managerId','total_job_records']], on='userId', how='left')


# ── 5. Performance ────────────────────────────────────────────────────────────

perf_df = pd.DataFrame([{
    'userId':    str(r['formSubjectId']),
    'rating':    float(r['rating']) if r.get('rating') and str(r['rating']).replace('.','').isdigit() and float(r['rating']) > 0 else None,
    'reviewEnd': parse_date(r.get('formReviewEndDate')),
} for r in perf_raw])

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


# ── 6. Compensation recency ───────────────────────────────────────────────────

comp_df = pd.DataFrame([{
    'userId':      r['userId'],
    'compDate':    parse_date(r['startDate']),
    'eventReason': (r.get('eventReason') or '').upper(),
    'bonusTarget': float(r['bonusTarget']) if r.get('bonusTarget') else None,
} for r in comp_raw])

comp_latest = (
    comp_df.sort_values('compDate', ascending=False)
    .groupby('userId').first().reset_index()
)
comp_latest['months_since_comp_change'] = comp_latest['compDate'].apply(months_since)
comp_latest['only_hire_comp'] = (comp_latest['eventReason'] == 'HIRNEW').astype(int)
active = active.merge(comp_latest[['userId','months_since_comp_change','bonusTarget','only_hire_comp']], on='userId', how='left')


# ── 7. Pay value ──────────────────────────────────────────────────────────────

pay_df = pd.DataFrame([{
    'userId':    r['userId'],
    'value':     float(r['paycompvalue']) if r.get('paycompvalue') else None,
    'frequency': r.get('frequency'),
} for r in pay_raw])

base_pay = (
    pay_df[pay_df['frequency'] == 'ANNUAL']
    .groupby('userId')['value'].max()
    .reset_index(name='base_salary')
)
active = active.merge(base_pay, on='userId', how='left')


# ── 8. Weighted risk score ────────────────────────────────────────────────────
# Each factor contributes 0–100 to a sub-score; final score is weighted average.

def safe(series, default=0):
    return series.fillna(default)

GLOBAL_TENURE_MED = active['tenure_years'].median() or 5

# Factor 1 — Role tenure stagnation (high risk: >3 yrs no change)
active['f_role_stagnation'] = np.clip(safe(active['tenure_in_role_years'], 0) / 5 * 100, 0, 100)

# Factor 2 — Low / no performance rating (scale 1–5 assumed; invert)
active['f_low_perf'] = np.where(
    active['perf_rating'].isna(), 60,  # no review = moderate-high risk
    np.clip((5 - safe(active['perf_rating'], 3)) / 4 * 100, 0, 100)
)

# Factor 3 — Stale compensation (>24 months since last change)
active['f_stale_comp'] = np.clip(safe(active['months_since_comp_change'], 36) / 36 * 100, 0, 100)

# Factor 4 — No salary increase since hire
active['f_only_hire'] = safe(active['only_hire_comp'], 0) * 100

# Factor 5 — Short company tenure (first 2 years = high risk)
active['f_short_tenure'] = np.clip((2 - np.minimum(safe(active['tenure_years'], 0), 2)) / 2 * 100, 0, 100)

WEIGHTS = {
    'f_role_stagnation':  0.25,
    'f_low_perf':         0.25,
    'f_stale_comp':       0.20,
    'f_only_hire':        0.15,
    'f_short_tenure':     0.15,
}

active['risk_score'] = sum(
    active[col] * weight for col, weight in WEIGHTS.items()
)

active['attrition_prob'] = active['risk_score'] / 100

active['risk_band'] = pd.cut(
    active['risk_score'],
    bins=[0, 30, 60, 101],
    labels=['Low', 'Medium', 'High'],
    include_lowest=True
)

print("\nRisk band distribution:")
print(active['risk_band'].value_counts())


# ── 9. Visualisations ─────────────────────────────────────────────────────────

print("\nGenerating visualizations...")
plt.style.use('seaborn-v0_8-whitegrid')
fig = plt.figure(figsize=(20, 26))
fig.suptitle('Flight Attrition Risk Analysis\nSAP SuccessFactors | SFSALES010044 | August 2026',
             fontsize=18, fontweight='bold', y=0.99)
gs = gridspec.GridSpec(4, 2, figure=fig, hspace=0.5, wspace=0.35)

PALETTE = {'Low': '#27ae60', 'Medium': '#f39c12', 'High': '#e74c3c'}

# 1. Risk band donut
ax1 = fig.add_subplot(gs[0, 0])
band_counts = active['risk_band'].value_counts().reindex(['High', 'Medium', 'Low'])
wedges, texts, autotexts = ax1.pie(
    band_counts.values,
    labels=band_counts.index,
    colors=[PALETTE[b] for b in band_counts.index],
    autopct='%1.0f%%',
    startangle=90,
    wedgeprops={'width': 0.5},
    textprops={'fontsize': 12},
)
for at in autotexts:
    at.set_fontsize(11)
    at.set_fontweight('bold')
ax1.set_title(f'Risk Band Distribution\n(n={len(active)} active employees)', fontsize=13, fontweight='bold')
# Centre label
ax1.text(0, 0, f"{int((active['risk_band']=='High').sum())}\nHigh Risk",
         ha='center', va='center', fontsize=14, fontweight='bold', color='#e74c3c')

# 2. Risk score distribution
ax2 = fig.add_subplot(gs[0, 1])
for band, colour in PALETTE.items():
    subset = active[active['risk_band'] == band]['risk_score']
    ax2.hist(subset, bins=15, alpha=0.75, color=colour, label=f'{band} ({len(subset)})', density=False)
ax2.axvline(active['risk_score'].mean(), color='black', linestyle='--', lw=1.5,
            label=f'Mean: {active["risk_score"].mean():.0f}')
ax2.set_title('Risk Score Distribution', fontsize=13, fontweight='bold')
ax2.set_xlabel('Risk Score (0–100)')
ax2.set_ylabel('Number of Employees')
ax2.legend()

# 3. Factor contribution (mean scores for High-risk cohort)
ax3 = fig.add_subplot(gs[1, :])
factor_labels = {
    'f_role_stagnation': 'Role Stagnation\n(tenure in role)',
    'f_low_perf':        'Low Performance\nRating',
    'f_stale_comp':      'Stale Compensation\n(months since raise)',
    'f_only_hire':       'No Raise Since\nHiring',
    'f_short_tenure':    'Short Company\nTenure (<2 yrs)',
}
high_risk_mean = active[active['risk_band'] == 'High'][[*WEIGHTS.keys()]].mean()
all_mean       = active[[*WEIGHTS.keys()]].mean()
x = np.arange(len(WEIGHTS))
width = 0.35
b1 = ax3.bar(x - width/2, all_mean.values,      width, label='All Employees',   color='#3498db', alpha=0.8)
b2 = ax3.bar(x + width/2, high_risk_mean.values, width, label='High-Risk Cohort', color='#e74c3c', alpha=0.8)
ax3.set_xticks(x)
ax3.set_xticklabels([factor_labels[k] for k in WEIGHTS.keys()], fontsize=10)
ax3.set_ylabel('Sub-Score (0–100)')
ax3.set_title('Risk Factor Sub-Scores: All vs High-Risk Cohort', fontsize=13, fontweight='bold')
ax3.legend()
ax3.set_ylim(0, 110)
for bar in list(b1) + list(b2):
    ax3.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
             f'{bar.get_height():.0f}', ha='center', va='bottom', fontsize=8)

# 4. Risk by department (top 12)
ax4 = fig.add_subplot(gs[2, 0])
dept_risk = (active.groupby('department')['risk_score'].mean()
             .sort_values(ascending=False).head(12))
colors_d = [PALETTE['High'] if v > 60 else PALETTE['Medium'] if v > 30 else PALETTE['Low']
            for v in dept_risk.values]
ax4.barh(dept_risk.index[::-1], dept_risk.values[::-1], color=colors_d[::-1])
ax4.axvline(active['risk_score'].mean(), color='gray', linestyle='--', lw=1, label='Avg')
ax4.set_title('Avg Risk Score by Department', fontsize=13, fontweight='bold')
ax4.set_xlabel('Avg Risk Score')
ax4.legend(fontsize=9)

# 5. Tenure vs role tenure scatter
ax5 = fig.add_subplot(gs[2, 1])
plot_df = active.dropna(subset=['tenure_years','tenure_in_role_years'])
n_plot = min(400, len(plot_df))
plot_sample = plot_df.sample(n_plot, random_state=42)
sc = ax5.scatter(
    plot_sample['tenure_years'],
    plot_sample['tenure_in_role_years'],
    c=plot_sample['risk_score'],
    cmap='RdYlGn_r', alpha=0.7, s=50, vmin=0, vmax=100
)
plt.colorbar(sc, ax=ax5, label='Risk Score')
ax5.set_title('Company Tenure vs Role Tenure\n(colour = risk score)', fontsize=13, fontweight='bold')
ax5.set_xlabel('Company Tenure (years)')
ax5.set_ylabel('Tenure in Current Role (years)')

# 6. Top 20 high-risk employees table
ax6 = fig.add_subplot(gs[3, :])
ax6.axis('off')
top20 = (
    active[active['risk_band'] == 'High']
    .sort_values('risk_score', ascending=False)
    .head(20)[['firstName','lastName','department','division',
               'tenure_years','tenure_in_role_years','perf_rating',
               'months_since_comp_change','risk_score']]
    .copy()
)
top20.columns = ['First','Last','Department','Division',
                 'Co.Tenure\n(yrs)','Role Tenure\n(yrs)',
                 'Perf\nRating','Months Since\nRaise','Risk\nScore']
top20 = top20.round(1)
tbl = ax6.table(
    cellText=top20.values,
    colLabels=top20.columns,
    cellLoc='center',
    loc='center',
    bbox=[0, 0, 1, 1]
)
tbl.auto_set_font_size(False)
tbl.set_fontsize(8)
for (row, col), cell in tbl.get_celld().items():
    if row == 0:
        cell.set_facecolor('#2c3e50')
        cell.set_text_props(color='white', fontweight='bold')
    elif row % 2 == 0:
        cell.set_facecolor('#f8f9fa')
    # Highlight risk score column
    if col == len(top20.columns) - 1 and row > 0:
        score = float(top20.values[row-1][-1])
        cell.set_facecolor('#e74c3c' if score > 75 else '#f39c12')
        cell.set_text_props(fontweight='bold', color='white')
ax6.set_title('Top 20 High-Risk Employees', fontsize=13, fontweight='bold', pad=20)

plt.savefig(OUTPUT / 'attrition_analysis.png', dpi=150, bbox_inches='tight')
plt.close()
print("  Saved: attrition_analysis.png")


# ── 10. Export ────────────────────────────────────────────────────────────────

export_cols = ['userId','firstName','lastName','department','division','location',
               'tenure_years','tenure_in_role_years','perf_rating',
               'months_since_comp_change','only_hire_comp','review_count',
               'risk_score','risk_band']
active[export_cols].sort_values('risk_score', ascending=False).to_csv(
    OUTPUT / 'all_employees_risk_scored.csv', index=False
)
print("  Saved: all_employees_risk_scored.csv")

summary = {
    'generated_at': NOW.isoformat(),
    'approach': 'Weighted risk scoring (5 factors)',
    'total_active_employees': int(len(active)),
    'risk_bands': {
        'high':   int((active['risk_band'] == 'High').sum()),
        'medium': int((active['risk_band'] == 'Medium').sum()),
        'low':    int((active['risk_band'] == 'Low').sum()),
    },
    'avg_risk_score': round(float(active['risk_score'].mean()), 1),
    'factors_and_weights': WEIGHTS,
    'top_risk_departments': dept_risk.head(5).round(1).to_dict(),
}
with open(OUTPUT / 'attrition_summary.json', 'w') as f:
    json.dump(summary, f, indent=2)
print("  Saved: attrition_summary.json")

print("\n=== SUMMARY ===")
print(json.dumps(summary, indent=2))

print(f"\n=== TOP 10 HIGH-RISK EMPLOYEES ===")
print(active.sort_values('risk_score', ascending=False)
      .head(10)[['firstName','lastName','department','risk_score','risk_band']]
      .to_string(index=False))
