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
import yaml

warnings.filterwarnings('ignore')

SFSF    = Path(__file__).parent.parent / 'fixtures' / 'sfsf'
PAYROLL = Path(__file__).parent.parent / 'fixtures' / 'payroll'
OUTPUT  = Path(__file__).parent.parent / 'fixtures' / 'output'
NOW     = datetime.now(timezone.utc)

_cfg_path = Path(__file__).parent / 'config.yaml'
with open(_cfg_path) as _f:
    _CFG = yaml.safe_load(_f)

WEIGHTS   = _CFG['weights']
_thresholds = _CFG['thresholds']
_bands    = _CFG['risk_bands']
_dq       = _CFG['data_quality']
FAR_FUTURE_MS = 253402214400000

FREQ_TO_ANNUAL = {'ANN': 1, 'MON': 12, 'SMT': 24, 'BWK': 26, 'BIM': 6, 'WKL': 52, 'HOURLY': 2080}

BASE_COMP_KEYWORDS = ['BASESAL','BASIC','BASE_','EEB_','BASAL','SALARIO']

def parse_date(val):
    if not val: return None
    if isinstance(val, str) and val.startswith('/Date('):
        inner = val.replace('/Date(', '').split(')')[0]
        if '+' in inner:
            inner = inner.split('+')[0]
        try:
            ms = int(inner)
        except ValueError:
            return None
        if ms >= FAR_FUTURE_MS: return None
        try:
            return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    return None

def years_since(dt):
    return max((NOW - dt).days / 365.25, 0) if dt else None

def months_since(dt):
    return max((NOW - dt).days / 30.44, 0) if dt else None

def load(folder, name):
    p = folder / f'{name}.json'
    if not p.exists():
        return []
    try:
        return json.load(open(p)).get('d', {}).get('results', [])
    except (json.JSONDecodeError, KeyError):
        return []


# ══════════════════════════════════════════════════════════════════
# 1. SFSF BASE DATA
# ══════════════════════════════════════════════════════════════════
print("Loading SuccessFactors data...")

# Active employees — fall back to payroll py-employees if SFSF fixture is missing/corrupt
_emp_active = load(SFSF, 'employees')
if not _emp_active:
    _emp_active = load(PAYROLL, 'py-employees')
    if _emp_active:
        print("  WARNING: SFSF employees.json unavailable — using payroll py-employees as fallback")

# Inactive/terminated employees (fetched separately with status=inactive filter)
_emp_inactive_path = SFSF / 'employees-inactive.json'
_emp_inactive = load(SFSF, 'employees-inactive') if _emp_inactive_path.exists() else []
if _emp_inactive:
    print(f"  Inactive employees loaded: {len(_emp_inactive)}")

_all_emp = _emp_active + _emp_inactive

# Employees — combine active + inactive for full population
df = pd.DataFrame([{
    'userId':     r['userId'],
    'firstName':  r.get('firstName', r.get('userId', 'Unknown')),
    'lastName':   r.get('lastName', ''),
    'department': (r.get('department') or 'Unknown').split('(')[0].strip(),
    'division':   (r.get('division') or 'Unknown').split('(')[0].strip(),
    'location':   (r.get('location') or 'Unknown').split('(')[0].strip(),
    'hireDate':   parse_date(r.get('hireDate')),
} for r in _all_emp])
df['tenure_years'] = df['hireDate'].apply(years_since)

# Attrition label — fall back to payroll py-employment if SFSF employment is unavailable
_empl_recs = load(SFSF, 'employment')
if not _empl_recs:
    _empl_recs = load(PAYROLL, 'py-employment')
    if _empl_recs:
        print("  WARNING: SFSF employment.json unavailable — using payroll py-employment as fallback")
empl_df = pd.DataFrame([{
    'userId': r['userId'],
    'empEndDate': parse_date(r.get('endDate')),
} for r in _empl_recs])
empl_df['attrited'] = empl_df['empEndDate'].notna().astype(int)
df = df.merge(empl_df[['userId','attrited']], on='userId', how='left')
df['attrited'] = df['attrited'].fillna(0).astype(int)
active = df[df['attrited'] == 0].copy()

# Data quality guard — abort early rather than silently score a partial dataset
_min = _dq.get('min_active_employees', 50)
if len(active) < _min:
    raise RuntimeError(
        f"Data quality check failed: only {len(active)} active employees loaded "
        f"(minimum required: {_min}). Check fixture files and OData connectivity."
    )

# EmpJob — tenure in role (fall back to payroll py-emp-job if SFSF unavailable)
job_raw = load(SFSF, 'emp-job')
if not job_raw:
    job_raw = load(PAYROLL, 'py-emp-job')
    if job_raw:
        print("  WARNING: SFSF emp-job.json unavailable — using payroll py-emp-job as fallback")
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
if perf_df.empty or 'rating' not in perf_df.columns:
    perf_df = pd.DataFrame(columns=['userId', 'rating', 'reviewEnd'])
perf_latest = (
    perf_df[perf_df['rating'].notna()]
    .sort_values('reviewEnd', ascending=False)
    .groupby('userId').first().reset_index()
    .rename(columns={'rating': 'perf_rating', 'reviewEnd': 'lastReviewDate'})
)
active = active.merge(perf_latest[['userId','perf_rating','lastReviewDate']] if not perf_latest.empty else pd.DataFrame(columns=['userId','perf_rating','lastReviewDate']), on='userId', how='left')
active['months_since_review'] = active['lastReviewDate'].apply(months_since) if 'lastReviewDate' in active.columns else np.nan
review_count = perf_df[perf_df['rating'].notna()].groupby('userId').size().reset_index(name='review_count') if not perf_df.empty else pd.DataFrame(columns=['userId','review_count'])
active = active.merge(review_count, on='userId', how='left')
active['review_count'] = active['review_count'].fillna(0)

# Compensation recency (SFSF)
_comp_recs = load(SFSF, 'compensation')
comp_df = pd.DataFrame([{
    'userId': r['userId'],
    'compDate': parse_date(r['startDate']),
    'eventReason': (r.get('eventReason') or '').upper(),
} for r in _comp_recs] if _comp_recs else [])
if comp_df.empty or 'compDate' not in comp_df.columns:
    comp_df = pd.DataFrame(columns=['userId', 'compDate', 'eventReason'])
comp_latest = comp_df.sort_values('compDate', ascending=False).groupby('userId').first().reset_index() if not comp_df.empty else pd.DataFrame(columns=['userId','compDate','eventReason'])
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

# ── PTO balance (unused leave accrual) ─────────────────────────────────────
# High unused balance is a known pre-departure signal: employees accumulate
# leave before resigning to maximise payout or clear obligations.
ta_raw = load(SFSF, 'time-account')
tad_raw = []
for src in [(SFSF, 'time-account-detail')]:
    p = src[0] / f'{src[1]}.json'
    if p.exists():
        tad_raw.extend(load(*src))

uid_to_extcode = {r['userId']: r['externalCode'] for r in ta_raw if r.get('userId')}
extcode_to_uid = {v: k for k, v in uid_to_extcode.items()}

pto_balances: dict = defaultdict(float)
for r in tad_raw:
    key = r.get('TimeAccount_externalCode')
    amt = float(r.get('bookingAmount') or 0)
    btype = r.get('bookingType', '')
    if btype in ('ACCRUAL', 'ADJUSTMENT'):
        pto_balances[key] += amt
    elif btype in ('DEDUCTION', 'ABSENCE_DEDUCTION'):
        pto_balances[key] -= amt

user_pto: dict = defaultdict(float)
for extcode, bal in pto_balances.items():
    uid = extcode_to_uid.get(extcode)
    if uid:
        user_pto[uid] += max(bal, 0)

pto_df = pd.DataFrame(list(user_pto.items()), columns=['userId', 'pto_balance_days'])
active = active.merge(pto_df, on='userId', how='left')
active['pto_balance_days'] = active['pto_balance_days'].fillna(0)
print(f"Employees with PTO balance data: {(active['pto_balance_days'] > 0).sum()} "
      f"| Median: {active['pto_balance_days'].median():.1f} days "
      f"| >20 days: {(active['pto_balance_days'] > 20).sum()}")

# ── Internal job applications ───────────────────────────────────────────────
# Employees applying for internal roles may signal desire to move; if their
# applications are declined, external flight risk increases.
ja_raw = load(SFSF, 'job-applications')
ja_records = [{'userId': r['usersSysId']} for r in ja_raw if r.get('usersSysId')]
if ja_records:
    ja_df = pd.DataFrame(ja_records)
    ja_count = ja_df.groupby('userId').size().reset_index(name='internal_app_count')
    active = active.merge(ja_count, on='userId', how='left')
else:
    active['internal_app_count'] = 0
active['internal_app_count'] = active['internal_app_count'].fillna(0)
print(f"Employees with internal job applications: {(active['internal_app_count'] > 0).sum()}")

# ── SF-side recurring pay (additional salary source) ───────────────────────
# Augments payroll salary coverage with SF-side pay components
sf_pay_raw = load(SFSF, 'emp-pay-recurring') if (SFSF / 'emp-pay-recurring.json').exists() else []
if not sf_pay_raw:
    sf_pay_raw = load(PAYROLL, 'py-pay-recurring')
    if sf_pay_raw:
        print("  WARNING: SFSF emp-pay-recurring.json unavailable — using payroll py-pay-recurring as fallback")
sf_base_records = []
for p in sf_pay_raw:
    comp = str(p.get('payComponent', '')).upper()
    if not any(kw in comp for kw in BASE_COMP_KEYWORDS):
        continue
    val = float(p['paycompvalue']) if p.get('paycompvalue') else 0
    freq = p.get('frequency', 'MON')
    annual = val * FREQ_TO_ANNUAL.get(freq, 12)
    if annual > 0:
        sf_base_records.append({'userId': p['userId'], 'sf_annual_salary': annual, 'sf_currency': p.get('currencyCode')})
sf_pay_df = pd.DataFrame(sf_base_records)
if not sf_pay_df.empty:
    sf_pay_max = sf_pay_df.groupby('userId').agg(sf_annual_salary=('sf_annual_salary','max'), sf_currency=('sf_currency','first')).reset_index()
else:
    sf_pay_max = pd.DataFrame(columns=['userId','sf_annual_salary','sf_currency'])
active = active.merge(sf_pay_max, on='userId', how='left')

# ── Pay grade via position linkage ─────────────────────────────────────────
# Links each employee's position (from EmpJob) to a pay grade (from Position).
pos_raw = load(SFSF, 'position') if (SFSF / 'position.json').exists() else []
pos_grade_map = {r['code']: r.get('payGrade') for r in pos_raw if r.get('payGrade')}
ej_pos = {r['userId']: str(r.get('position') or '') for r in job_raw if r.get('position')}
active['pay_grade'] = active['userId'].map(lambda uid: pos_grade_map.get(ej_pos.get(uid, ''), None))
grade_coverage = active['pay_grade'].notna().sum()
print(f"Pay grade coverage: {grade_coverage}/{len(active)} employees | Unique grades: {active['pay_grade'].nunique()}")

# ── Gender from PerPersonal (reporting only, not used in scoring) ───────────
pp_raw = load(SFSF, 'per-personal') if (SFSF / 'per-personal.json').exists() else []
gender_df = pd.DataFrame([{'userId': str(r['personIdExternal']), 'gender': r.get('gender')} for r in pp_raw if r.get('gender')])
if not gender_df.empty:
    active = active.merge(gender_df, on='userId', how='left')
else:
    active['gender'] = None
print(f"Gender data coverage: {active['gender'].notna().sum()}")

# ── Calibration coverage ────────────────────────────────────────────────────
# Employees included in a calibration session are actively tracked by management.
# Those NOT in calibration have lower visibility — a mild risk signal.
calib_raw = load(SFSF, 'calibration-subjects') if (SFSF / 'calibration-subjects.json').exists() else []
calib_uids = set(str(r.get('userId', '')) for r in calib_raw if r.get('userId'))
active['in_calibration'] = active['userId'].isin(calib_uids).astype(int)
print(f"Employees in calibration sessions: {active['in_calibration'].sum()}")

# ── Open job requisitions in department ─────────────────────────────────────
# Active job requisitions in a department signal team gaps / attrition pressure.
# Employees in understaffed departments are more likely to be overloaded and leave.
jr_raw = load(SFSF, 'job-requisitions') if (SFSF / 'job-requisitions.json').exists() else []
open_jr = [r for r in jr_raw if r.get('deleted') == 'Not Deleted']
dept_open_reqs: dict = defaultdict(int)
for r in open_jr:
    dept_raw = r.get('departmentCode', '') or ''
    dept = dept_raw.split('(')[0].strip()
    if dept:
        dept_open_reqs[dept] += 1
active['open_reqs_in_dept'] = active['department'].map(dept_open_reqs).fillna(0)
print(f"Employees in depts with open requisitions: {(active['open_reqs_in_dept'] > 0).sum()}")

# ── Age / Career stage (from PerPerson) ────────────────────────────────────
# Early-career employees (<32) have higher market mobility and are more likely
# to leave for new opportunities. Note: use with caution — age discrimination
# laws apply; this signal informs statistical prediction only.
pp_raw = load(SFSF, 'per-person') if (SFSF / 'per-person.json').exists() else []
age_rows = []
for r in pp_raw:
    dob_val = r.get('dateOfBirth')
    if dob_val:
        dob = parse_date(dob_val)
        if dob:
            age = max((NOW - dob).days / 365.25, 0)
            if 15 < age < 80:
                age_rows.append({'userId': str(r['personIdExternal']), 'age_years': age})
age_df = pd.DataFrame(age_rows) if age_rows else pd.DataFrame(columns=['userId','age_years'])
active = active.merge(age_df, on='userId', how='left')
active['age_years'] = active['age_years'].fillna(active['age_years'].median() if not age_df.empty else 40)
print(f"Employees with age data: {age_df['userId'].nunique()} | Median age: {active['age_years'].median():.0f}")

# ── Unmet bonus expectation (from EmpCompensation) ─────────────────────────
# Employees with a bonus target in SF but no bonus paid in payroll have an
# unmet expectation — a well-documented voluntary exit trigger.
ec_raw = load(SFSF, 'emp-compensation') if (SFSF / 'emp-compensation.json').exists() else []
ec_df = pd.DataFrame([{
    'userId': r['userId'],
    'bonus_target': float(r['bonusTarget']) if r.get('bonusTarget') else 0,
} for r in ec_raw])
if not ec_df.empty:
    # Keep latest record per employee
    ec_latest = ec_df[ec_df['bonus_target'] > 0].groupby('userId')['bonus_target'].max().reset_index()
    active = active.merge(ec_latest, on='userId', how='left')
else:
    active['bonus_target'] = 0
active['bonus_target'] = active['bonus_target'].fillna(0)
bt_with_no_payout = ((active['bonus_target'] > 0) & (active['has_bonus'] == 0)).sum()
print(f"Employees with bonus target but no payout: {bt_with_no_payout}")

# ── Pay group (from EmpCompensation.payGroup) ───────────────────────────────
# Pay group classifies employees into compensation pools (e.g. 'US', 'D2', 'CN').
# Employees below their pay group median are outliers within the same comp class —
# more precise than department median because pay groups cut across role families.
pg_df = pd.DataFrame([{
    'userId': r['userId'],
    'pay_group': r.get('payGroup'),
} for r in ec_raw if r.get('payGroup')])
if not pg_df.empty:
    pg_latest = pg_df.drop_duplicates('userId', keep='first')
    active = active.merge(pg_latest, on='userId', how='left')
else:
    active['pay_group'] = None
pg_coverage = active['pay_group'].notna().sum()
print(f"Pay group coverage: {pg_coverage}/{len(active)} | Unique groups: {active['pay_group'].nunique()}")

# Best salary estimate: payroll direct → payroll recurring → SF-side recurring
active['base_salary'] = active['salary_direct'].combine_first(active['annual_salary_pay']).combine_first(active['sf_annual_salary'])

# Compa-ratio proxy: salary vs dept median (within same currency group)
dept_median = active.groupby('department')['base_salary'].median().reset_index(name='dept_median_salary')
active = active.merge(dept_median, on='department', how='left')
active['compa_ratio'] = np.where(
    active['dept_median_salary'] > 0,
    active['base_salary'] / active['dept_median_salary'],
    np.nan
)
active['below_market'] = (active['compa_ratio'] < _thresholds['compa_below_market']).astype(float)

# Pay-group compa-ratio: salary vs pay group median (more precise than dept median)
pg_median = active.groupby('pay_group')['base_salary'].median().reset_index(name='pg_median_salary')
active = active.merge(pg_median, on='pay_group', how='left')
active['pay_group_compa_ratio'] = np.where(
    active['pg_median_salary'] > 0,
    active['base_salary'] / active['pg_median_salary'],
    np.nan
)

salary_coverage = active['base_salary'].notna().sum()
print(f"Salary coverage: {salary_coverage}/{len(active)} employees ({salary_coverage/len(active)*100:.0f}%)")
print(f"Avg compa-ratio: {active['compa_ratio'].mean():.2f}")
print(f"Below market (<0.9): {active['below_market'].sum():.0f} employees")
print(f"Employees with bonus history: {active['has_bonus'].sum():.0f}")

# ── FTE / part-time signal (from EmpJobGrade) ──────────────────────────────
# Employees with FTE < 0.8 are either on reduced schedules or had hours cut.
# Both scenarios correlate with lower engagement or involuntary restructuring.
# FTE > 2.0 is assumed to be bad data (multiple contract rows summed) and capped.
ej_grade_raw = load(SFSF, 'emp-job-grade') if (SFSF / 'emp-job-grade.json').exists() else []
if ej_grade_raw:
    fte_rows = []
    for r in ej_grade_raw:
        if r.get('fte') is not None:
            try:
                fte_val = float(r['fte'])
                if 0 < fte_val <= 1.0:
                    fte_rows.append({'userId': r['userId'], 'fte': fte_val})
            except (ValueError, TypeError):
                pass
    if fte_rows:
        fte_df = pd.DataFrame(fte_rows)
        fte_latest = fte_df.groupby('userId')['fte'].min().reset_index()
        active = active.merge(fte_latest, on='userId', how='left')
    else:
        active['fte'] = None
else:
    active['fte'] = None
active['fte'] = active['fte'].fillna(1.0)
part_time_n = int((active['fte'] < _thresholds.get('part_time_fte', 0.8)).sum())
print(f"Part-time employees (FTE < {_thresholds.get('part_time_fte', 0.8)}): {part_time_n}")

# ── Mentoring engagement (from MentoringProgramMatchedParticipant) ──────────
# Being an active mentor or mentee is a strong protective signal: engaged employees
# in mentoring relationships are significantly less likely to leave.
mentoring_raw = load(SFSF, 'mentoring2') if (SFSF / 'mentoring2.json').exists() else []
if not mentoring_raw:
    mentoring_raw = load(SFSF, 'mentoring_full') if (SFSF / 'mentoring_full.json').exists() else []
mentor_uids  = set(str(r['mentor'])  for r in mentoring_raw if r.get('mentor'))
mentee_uids  = set(str(r['mentee'])  for r in mentoring_raw if r.get('mentee'))
mentoring_uids = mentor_uids | mentee_uids
active['is_mentor'] = active['userId'].isin(mentor_uids).astype(int)
active['is_mentee'] = active['userId'].isin(mentee_uids).astype(int)
active['in_mentoring'] = active['userId'].isin(mentoring_uids).astype(int)
print(f"Mentors: {len(mentor_uids)} | Mentees: {len(mentee_uids)} | In mentoring: {active['in_mentoring'].sum()}")


# ══════════════════════════════════════════════════════════════════
# 3. ENRICHED RISK SCORING
# ══════════════════════════════════════════════════════════════════
print("\nCalculating enriched risk scores...")

def safe(s, default=0):
    return s.fillna(default)

# Original 5 factors (60% weight)
active['f_role_stagnation']  = np.clip(safe(active['tenure_in_role_years'], 0) / _thresholds['role_stagnation_years'] * 100, 0, 100)
active['f_low_perf']         = np.where(active['perf_rating'].isna(), 60,
                                np.clip((5 - safe(active['perf_rating'], 3)) / 4 * 100, 0, 100))
active['f_stale_comp']       = np.clip(safe(active['months_since_comp_change'], _thresholds['stale_comp_months']) / _thresholds['stale_comp_months'] * 100, 0, 100)
active['f_only_hire']        = safe(active['only_hire_comp'], 0) * 100
active['f_short_tenure']     = np.clip((2 - np.minimum(safe(active['tenure_years'], 0), 2)) / 2 * 100, 0, 100)

# Payroll: compa-ratio — continuous scale, cr<0.7→100, cr=1.1→0
active['f_compa_ratio']      = np.where(
    active['compa_ratio'].isna(), 50,
    np.clip((1.1 - active['compa_ratio']) / 0.6 * 100, 0, 100)
)
# No bonus history → lack of recognition/variable pay
active['f_no_bonus']         = (1 - safe(active['has_bonus'], 0)) * 70

active['f_high_absence']     = np.clip(safe(active['absence_count'], 0) / 10 * 100, 0, 100)

active['f_mgr_instability']  = np.clip(safe(active['mgr_change_count'], 0) / _thresholds['mgr_instability_changes'] * 100, 0, 100)

active['f_high_pto_balance'] = np.clip(safe(active['pto_balance_days'], 0) / _thresholds['pto_high_days'] * 100, 0, 100)

active['f_internal_application'] = np.where(safe(active['internal_app_count'], 0) > 0, 60, 0)

_ec_max = _thresholds['early_career_max_age']
_ec_min = _thresholds['early_career_min_age']
active['f_early_career'] = np.clip((_ec_max - safe(active['age_years'], 40)) / (_ec_max - _ec_min) * 100, 0, 100)

active['f_unmet_bonus'] = np.where(
    (safe(active['bonus_target'], 0) > 0) & (safe(active['has_bonus'], 0) == 0), 80, 0
)

active['f_not_calibrated'] = (1 - safe(active['in_calibration'], 0)) * 30

active['f_open_req_in_dept'] = np.where(safe(active['open_reqs_in_dept'], 0) > 0, 50, 0)

active['f_pay_group_compa'] = np.where(
    active['pay_group_compa_ratio'].notna(),
    np.clip((1.1 - active['pay_group_compa_ratio']) / 0.6 * 100, 0, 100),
    active['f_compa_ratio']
)

# Part-time / reduced FTE — employees below threshold get a moderate risk score;
# full-time employees score 0. Signals reduced engagement or involuntary hour cuts.
_pt_thresh = _thresholds.get('part_time_fte', 0.8)
active['f_part_time'] = np.where(safe(active['fte'], 1.0) < _pt_thresh, 40, 0)

# Employees not in any mentoring relationship score a mild risk penalty;
# engaged mentors/mentees score 0 (protective — no risk contribution).
active['f_not_in_mentoring'] = np.where(safe(active['in_mentoring'], 0) == 0, 25, 0)

# Weights loaded from model/config.yaml
active['risk_score'] = sum(active[col] * w for col, w in WEIGHTS.items())
active['risk_band']  = pd.cut(active['risk_score'],
                               bins=[0, _bands['low_max'], _bands['medium_max'], 101],
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
    'f_role_stagnation':      'Role\nStagnation',
    'f_low_perf':             'Low\nPerformance',
    'f_compa_ratio':          'Below\nMarket Pay ★',
    'f_not_in_mentoring':     'Not in\nMentoring',
    'f_stale_comp':           'Stale\nCompensation',
    'f_only_hire':            'No Raise\nSince Hire',
    'f_short_tenure':         'Short\nTenure',
    'f_no_bonus':             'No Bonus\nHistory ★',
    'f_high_absence':         'High\nAbsence ★',
    'f_mgr_instability':      'Manager\nInstability ★',
    'f_high_pto_balance':     'High PTO\nBalance ★',
    'f_internal_application': 'Internal\nApplications ★',
    'f_early_career':         'Early\nCareer Stage ★',
    'f_unmet_bonus':          'Unmet Bonus\nExpectation ★',
    'f_not_calibrated':       'Not in\nCalibration ★',
    'f_open_req_in_dept':     'Open Reqs\nin Dept ★',
    'f_pay_group_compa':      'Pay Group\nCompa ★',
    'f_part_time':            'Part-Time /\nReduced FTE ★',
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
if len(top20) > 0:
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
else:
    ax6.text(0.5, 0.5, 'No High-risk employees this run', ha='center', va='center',
             fontsize=14, color='#27ae60', fontweight='bold', transform=ax6.transAxes)
ax6.set_title('Top 20 High-Risk Employees (Enriched with Payroll Data)', fontsize=13, fontweight='bold', pad=20)

plt.savefig(OUTPUT / 'attrition_enriched.png', dpi=150, bbox_inches='tight')
plt.close()
print("  Saved: attrition_enriched.png")


# ══════════════════════════════════════════════════════════════════
# 5. EXPORTS
# ══════════════════════════════════════════════════════════════════
export_cols = ['userId','firstName','lastName','department','division','location',
               'gender','pay_grade','pay_group',
               'tenure_years','tenure_in_role_years','perf_rating',
               'months_since_comp_change','only_hire_comp','review_count',
               'base_salary','currency','compa_ratio','pay_group_compa_ratio','has_bonus','bonus_events',
               'bonus_target','age_years',
               'absence_count','total_leave_days','loa_flag','sick_count',
               'mgr_change_count','org_change_count','title_change_count',
               'pto_balance_days','internal_app_count',
               'in_calibration','open_reqs_in_dept',
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
    'employees_with_high_pto_balance': int((active['pto_balance_days'] > 20).sum()),
    'employees_with_internal_applications': int((active['internal_app_count'] > 0).sum()),
    'employees_with_unmet_bonus_target': int(bt_with_no_payout),
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


# ══════════════════════════════════════════════════════════════════
# 6. BACKTEST — score historical leavers, measure separation
# ══════════════════════════════════════════════════════════════════
print("\n=== BACKTEST: Scoring Historical Leavers ===")

terminated = df[df['attrited'] == 1].copy()

# If employees-inactive.json is available, terminated profiles are already in df.
# Otherwise reconstruct basic profiles from emp-job-history for the missing user IDs.
term_ids_in_df = set(terminated['userId'].tolist())
all_term_ids = {
    r['userId']
    for r in load(SFSF, 'employment')
    if parse_date(r.get('endDate'))
}
# Augment with payroll employment: lastDateWorked and okToRehire=False
_py_emp_raw = load(PAYROLL, 'py-employment')
_py_term_ids = {
    r['userId'] for r in _py_emp_raw
    if r.get('lastDateWorked') and '/Date(-' not in str(r.get('lastDateWorked', ''))
    and parse_date(r.get('lastDateWorked'))
}
_py_bad_exit_ids = {r['userId'] for r in _py_emp_raw if r.get('okToRehire') is False}
all_term_ids = all_term_ids | _py_term_ids | _py_bad_exit_ids
missing_ids = all_term_ids - term_ids_in_df

if missing_ids:
    # Build minimal profiles from emp-job-history (has dept/title) + employment (has tenure)
    hist_by_uid: dict = defaultdict(list)
    for r in job_hist_recs:
        if r['userId'] in missing_ids:
            hist_by_uid[r['userId']].append(r)

    emp_end_dates = {
        r['userId']: parse_date(r['endDate'])
        for r in load(SFSF, 'employment')
        if parse_date(r.get('endDate'))
    }
    emp_start_dates = {
        r['userId']: parse_date(r.get('startDate'))
        for r in load(SFSF, 'employment')
        if r.get('startDate')
    }

    extra_rows = []
    for uid in missing_ids:
        jobs = sorted(hist_by_uid.get(uid, []), key=lambda x: x['seq'])
        last = jobs[-1] if jobs else {}
        hire = emp_start_dates.get(uid)
        end  = emp_end_dates.get(uid)
        extra_rows.append({
            'userId':     uid,
            'firstName':  uid,   # no profile — use userId as placeholder
            'lastName':   '(inactive)',
            'department': last.get('dept') or 'Unknown',
            'division':   'Unknown',
            'location':   'Unknown',
            'hireDate':   hire,
            'tenure_years': years_since(hire) if hire else None,
            'attrited':   1,
        })

    if extra_rows:
        extra_df = pd.DataFrame(extra_rows)
        terminated = pd.concat([terminated, extra_df], ignore_index=True)
        print(f"  Reconstructed {len(extra_rows)} terminated profiles from emp-job-history")

print(f"Terminated employees found: {len(terminated)}")

if len(terminated) > 0:
    terminated = terminated.merge(job_latest[['userId','tenure_in_role_years','managerId','total_job_records']], on='userId', how='left')
    terminated = terminated.merge(perf_latest[['userId','perf_rating','lastReviewDate']], on='userId', how='left')
    terminated['months_since_review'] = terminated['lastReviewDate'].apply(months_since)
    terminated = terminated.merge(review_count, on='userId', how='left')
    terminated['review_count'] = terminated['review_count'].fillna(0)
    terminated = terminated.merge(comp_latest[['userId','months_since_comp_change','only_hire_comp']], on='userId', how='left')
    terminated = terminated.merge(py_users[['userId','salary_direct','dateOfCurrentPosition']], on='userId', how='left')
    terminated = terminated.merge(pay_salary_max, on='userId', how='left')
    terminated = terminated.merge(has_bonus, on='userId', how='left')
    terminated = terminated.merge(bonus_count, on='userId', how='left')
    terminated['has_bonus'] = terminated['has_bonus'].fillna(0)
    terminated['bonus_events'] = terminated['bonus_events'].fillna(0)
    terminated = terminated.merge(leave_summary, on='userId', how='left')
    for col in ['absence_count', 'total_leave_days', 'loa_flag', 'sick_count']:
        terminated[col] = terminated[col].fillna(0)
    terminated = terminated.merge(change_df, on='userId', how='left')
    for col in ['mgr_change_count', 'org_change_count', 'title_change_count']:
        terminated[col] = terminated[col].fillna(0)
    terminated = terminated.merge(pto_df, on='userId', how='left')
    terminated['pto_balance_days'] = terminated['pto_balance_days'].fillna(0)
    terminated['internal_app_count'] = 0
    terminated = terminated.merge(age_df, on='userId', how='left')
    terminated['age_years'] = terminated['age_years'].fillna(active['age_years'].median())
    terminated['bonus_target'] = 0
    terminated['in_calibration'] = 0
    terminated['open_reqs_in_dept'] = terminated['department'].map(dept_open_reqs).fillna(0)
    terminated = terminated.merge(sf_pay_max, on='userId', how='left')
    terminated['base_salary'] = (
        terminated['salary_direct']
        .combine_first(terminated['annual_salary_pay'])
        .combine_first(terminated['sf_annual_salary'])
    )
    terminated = terminated.merge(dept_median, on='department', how='left')
    terminated['compa_ratio'] = np.where(
        terminated['dept_median_salary'] > 0,
        terminated['base_salary'] / terminated['dept_median_salary'],
        np.nan
    )
    terminated['pay_group_compa_ratio'] = np.nan

    # Same scoring factors
    terminated['f_role_stagnation']      = np.clip(safe(terminated['tenure_in_role_years'], 0) / _thresholds['role_stagnation_years'] * 100, 0, 100)
    terminated['f_low_perf']             = np.where(terminated['perf_rating'].isna(), 60,
                                            np.clip((5 - safe(terminated['perf_rating'], 3)) / 4 * 100, 0, 100))
    terminated['f_stale_comp']           = np.clip(safe(terminated['months_since_comp_change'], _thresholds['stale_comp_months']) / _thresholds['stale_comp_months'] * 100, 0, 100)
    terminated['f_only_hire']            = safe(terminated['only_hire_comp'], 0) * 100
    terminated['f_short_tenure']         = np.clip((2 - np.minimum(safe(terminated['tenure_years'], 0), 2)) / 2 * 100, 0, 100)
    terminated['f_compa_ratio']          = np.where(terminated['compa_ratio'].isna(), 50, np.clip((1.1 - terminated['compa_ratio']) / 0.6 * 100, 0, 100))
    terminated['f_no_bonus']             = (1 - safe(terminated['has_bonus'], 0)) * 70
    terminated['f_high_absence']         = np.clip(safe(terminated['absence_count'], 0) / 10 * 100, 0, 100)
    terminated['f_mgr_instability']      = np.clip(safe(terminated['mgr_change_count'], 0) / _thresholds['mgr_instability_changes'] * 100, 0, 100)
    terminated['f_high_pto_balance']     = np.clip(safe(terminated['pto_balance_days'], 0) / _thresholds['pto_high_days'] * 100, 0, 100)
    terminated['f_internal_application'] = 0
    _ec_max = _thresholds['early_career_max_age']
    _ec_min = _thresholds['early_career_min_age']
    terminated['f_early_career']         = np.clip((_ec_max - safe(terminated['age_years'], 40)) / (_ec_max - _ec_min) * 100, 0, 100)
    terminated['f_unmet_bonus']          = 0
    terminated['f_not_calibrated']       = 30
    terminated['f_open_req_in_dept']     = np.where(safe(terminated['open_reqs_in_dept'], 0) > 0, 50, 0)
    terminated['f_pay_group_compa']      = terminated['f_compa_ratio']
    terminated['f_part_time']            = 0  # FTE data unavailable for reconstructed profiles
    terminated['f_not_in_mentoring']     = 25  # mentoring data unavailable for reconstructed profiles

    terminated['risk_score'] = sum(terminated[col] * w for col, w in WEIGHTS.items())
    terminated['risk_band']  = pd.cut(terminated['risk_score'],
                                       bins=[0, _bands['low_max'], _bands['medium_max'], 101],
                                       labels=['Low', 'Medium', 'High'], include_lowest=True)

    n_leavers  = len(terminated)
    n_high     = int((terminated['risk_band'] == 'High').sum())
    n_medium   = int((terminated['risk_band'] == 'Medium').sum())
    n_low      = int((terminated['risk_band'] == 'Low').sum())
    capture    = round((n_high + n_medium) / n_leavers * 100, 1)
    avg_leaver = round(float(terminated['risk_score'].mean()), 1)
    avg_active_bt = round(float(active['risk_score'].mean()), 1)

    score_bin_edges = list(range(0, 105, 10))
    score_labels_bt = [f'{b}–{b+10}' for b in score_bin_edges[:-1]]

    def _bin(scores):
        counts = [0] * len(score_labels_bt)
        for s in scores:
            counts[min(int(s // 10), len(counts)-1)] += 1
        return counts

    leaver_counts = _bin(terminated['risk_score'].tolist())
    active_counts = _bin(active['risk_score'].tolist())
    leaver_pct = [round(c / n_leavers * 100, 1) for c in leaver_counts]
    active_pct  = [round(c / len(active) * 100, 1) for c in active_counts]

    backtest = {
        'generated_at':     NOW.isoformat(),
        'n_leavers':        n_leavers,
        'n_high':           n_high,
        'n_medium':         n_medium,
        'n_low':            n_low,
        'capture_rate_pct': capture,
        'avg_leaver_score': avg_leaver,
        'avg_active_score': avg_active_bt,
        'score_separation': round(avg_leaver - avg_active_bt, 1),
        'leaver_scores':    terminated['risk_score'].round(1).tolist(),
        'score_labels':     score_labels_bt,
        'leaver_hist_pct':  leaver_pct,
        'active_hist_pct':  active_pct,
        'note': (
            f'n={n_leavers} historical leavers identified from employment.endDate. '
            'Scores use current data snapshots as proxies for pre-departure state — '
            'not true time-series reconstruction. Treat as directional signal only. '
            'Expand historical fixture data from SF Reports for higher-confidence validation.'
        ),
    }
    with open(OUTPUT / 'backtest_results.json', 'w') as f:
        json.dump(backtest, f, indent=2)

    print(f"Leavers: {n_leavers} | High: {n_high} | Medium: {n_medium} | Low: {n_low}")
    print(f"Capture rate (High+Medium): {capture}%")
    print(f"Avg score — leavers: {avg_leaver}  active: {avg_active_bt}  separation: +{avg_leaver - avg_active_bt:.1f}")
    print("  Saved: backtest_results.json")
else:
    backtest = None
    print("No terminated employees found — skipping backtest.")
