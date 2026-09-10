"""
RAG server for flight attrition conversational queries.
Indexes employee profiles in ChromaDB, answers HR questions via the local `claude` CLI.
Supports tool-calling loop for aggregate queries and SQLite-backed action tracking.

Usage:
    python model/rag_server.py
    # Server starts on http://localhost:5001
"""
import json
import math
import re
import sqlite3
import subprocess
import threading
import time
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, request, jsonify
from flask_cors import CORS
import chromadb
from rank_bm25 import BM25Okapi

FIXTURES    = Path(__file__).parent.parent / 'fixtures' / 'output'
ROOT        = Path(__file__).parent.parent
CSV_PATH    = FIXTURES / 'all_employees_enriched_risk.csv'
SUMMARY_PATH = FIXTURES / 'attrition_enriched_summary.json'
ACTIONS_DB  = Path(__file__).parent / 'actions.db'

app = Flask(__name__)
CORS(app)

# ── Action tracking DB ────────────────────────────────────────────────────────
def _init_db():
    con = sqlite3.connect(ACTIONS_DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS retention_actions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT NOT NULL,
            action_type TEXT NOT NULL,
            description TEXT,
            owner       TEXT,
            status      TEXT DEFAULT 'open',
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.commit()
    con.close()

_init_db()

def _db():
    con = sqlite3.connect(ACTIONS_DB)
    con.row_factory = sqlite3.Row
    return con


def _safe(v, default='—'):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return default
    return v


def _build_doc(row: dict) -> str:
    name = f"{_safe(row.get('firstName'), '')} {_safe(row.get('lastName'), '')}".strip()
    uid = str(row.get('userId', ''))
    dept = _safe(row.get('department'))
    div = _safe(row.get('division'))
    loc = _safe(row.get('location'))
    risk_score = _safe(row.get('risk_score'), 0)
    risk_band = _safe(row.get('risk_band'))
    tenure = _safe(row.get('tenure_years'))
    role_tenure = _safe(row.get('tenure_in_role_years'))
    perf = _safe(row.get('perf_rating'))
    months_comp = _safe(row.get('months_since_comp_change'))
    salary = _safe(row.get('base_salary'))
    currency = _safe(row.get('currency'), '')
    compa = _safe(row.get('compa_ratio'))
    pg_compa = _safe(row.get('pay_group_compa_ratio'))
    has_bonus = bool(row.get('has_bonus', 0))
    bonus_target = _safe(row.get('bonus_target'))
    bonus_events = _safe(row.get('bonus_events'), 0)
    absences = _safe(row.get('absence_count'), 0)
    sick = _safe(row.get('sick_count'), 0)
    pto = _safe(row.get('pto_balance_days'), 0)
    mgr_changes = _safe(row.get('mgr_change_count'), 0)
    title_changes = _safe(row.get('title_change_count'), 0)
    in_cal = bool(row.get('in_calibration', 0))
    open_reqs = _safe(row.get('open_reqs_in_dept'), 0)
    internal_apps = _safe(row.get('internal_app_count'), 0)
    review_count = _safe(row.get('review_count'), 0)
    age = _safe(row.get('age_years'))

    def _fmt_float(v, fmt): return fmt.format(float(v)) if v != '—' else '—'

    tenure_str = _fmt_float(tenure, '{:.1f}yr')
    role_str = _fmt_float(role_tenure, '{:.1f}yr')
    perf_str = _fmt_float(perf, '{:.1f}/5') if perf != '—' else 'no review'
    comp_str = f"{int(float(months_comp))}mo ago" if months_comp != '—' else '—'
    salary_str = f"{int(float(salary))} {currency}" if salary != '—' else '—'
    compa_str = _fmt_float(compa, '{:.3f}')
    pg_compa_str = _fmt_float(pg_compa, '{:.3f}')
    risk_str = f"{float(risk_score):.1f}/100"
    bonus_str = f"yes ({int(float(bonus_events))} events)" if has_bonus else "none"
    bonus_t_str = f"{int(float(bonus_target))}" if bonus_target not in ('—', 0, 0.0) else '—'
    pto_str = _fmt_float(pto, '{:.0f}d')
    age_str = _fmt_float(age, '{:.0f}yr')

    return (
        f"{name} ({uid}) | {dept} | {loc} | {div}\n"
        f"Risk: {risk_str} ({risk_band}) | Tenure: {tenure_str} | Role tenure: {role_str}\n"
        f"Salary: {salary_str} | Compa-ratio: {compa_str} | Pay group compa: {pg_compa_str}\n"
        f"Performance: {perf_str} | Reviews: {review_count} | Last pay change: {comp_str}\n"
        f"Absences: {absences} | Sick days: {sick} | PTO balance: {pto_str} | Age: {age_str}\n"
        f"Bonus: {bonus_str} | Bonus target: {bonus_t_str}\n"
        f"Manager changes: {mgr_changes} | Title changes: {title_changes}\n"
        f"Calibrated: {'yes' if in_cal else 'no'} | Open reqs in dept: {open_reqs} | Internal apps: {internal_apps}"
    )


# ── build index ────────────────────────────────────────────────────────────────
print("Loading employee data...")
df = pd.read_csv(CSV_PATH)
summary = json.loads(SUMMARY_PATH.read_text())

print(f"Building ChromaDB index for {len(df)} employees...")
_chroma = chromadb.Client()
collection = _chroma.create_collection("employees")

_batch_docs, _batch_ids, _batch_metas = [], [], []
_seen_ids: set[str] = set()

for i, (_, row) in enumerate(df.iterrows()):
    row_dict = row.to_dict()
    doc = _build_doc(row_dict)
    uid = str(row_dict.get('userId', ''))
    # Ensure unique IDs for ChromaDB
    chroma_id = uid if uid not in _seen_ids else f"{uid}_{i}"
    _seen_ids.add(chroma_id)

    risk_score_val = row_dict.get('risk_score', 0.0)
    if isinstance(risk_score_val, float) and math.isnan(risk_score_val):
        risk_score_val = 0.0

    _batch_docs.append(doc)
    _batch_ids.append(chroma_id)
    _batch_metas.append({
        'user_id': uid,
        'department': str(row_dict.get('department', '') or ''),
        'division': str(row_dict.get('division', '') or ''),
        'location': str(row_dict.get('location', '') or ''),
        'risk_band': str(row_dict.get('risk_band', '') or ''),
        'risk_score': float(risk_score_val),
    })

    if len(_batch_docs) >= 100:
        collection.upsert(documents=_batch_docs, ids=_batch_ids, metadatas=_batch_metas)
        _batch_docs, _batch_ids, _batch_metas = [], [], []

if _batch_docs:
    collection.upsert(documents=_batch_docs, ids=_batch_ids, metadatas=_batch_metas)

print(f"Indexed {collection.count()} employees")

# ── BM25 keyword index ─────────────────────────────────────────────────────────
_doc_list: list[str] = _batch_docs  # already empty — rebuild from collection
# Retrieve all docs for BM25 (ChromaDB doesn't expose a bulk-get, use our own list)
_all_docs: list[str] = []
_all_uids: list[str] = []
for i, (_, row) in enumerate(df.iterrows()):
    _all_docs.append(_build_doc(row.to_dict()))
    _all_uids.append(str(row.get('userId', '')))

_bm25 = BM25Okapi([doc.lower().split() for doc in _all_docs])
print(f"BM25 index built: {len(_all_docs)} docs")

# Pre-compute top 10 high-risk profiles for consistent context
_top_risk = df.nlargest(10, 'risk_score')
TOP_RISK_CONTEXT = "\n\n".join(_build_doc(r.to_dict()) for _, r in _top_risk.iterrows())

# ── hybrid retrieval helpers ──────────────────────────────────────────────────
_RANK_WORDS = {'top', 'highest', 'worst', 'most', 'riskiest', 'critical'}
_FACTOR_WORDS = {
    'stagnation': 'tenure_in_role_years', 'role': 'tenure_in_role_years',
    'salary': 'compa_ratio', 'pay': 'compa_ratio', 'compa': 'compa_ratio',
    'absence': 'absence_count', 'absent': 'absence_count',
    'performance': 'perf_rating', 'perf': 'perf_rating',
    'pto': 'pto_balance_days', 'leave': 'pto_balance_days',
    'manager': 'mgr_change_count', 'tenure': 'tenure_years',
}

def _extract_candidate_ids(query: str) -> list[str] | None:
    """Return filtered userId list from metadata keywords, or None to search all."""
    q = query.lower()
    mask = pd.Series([True] * len(df), index=df.index)
    filtered = False

    if any(w in q for w in ['high risk', 'high-risk', 'highest risk']):
        mask &= df['risk_band'] == 'High'
        filtered = True
    elif any(w in q for w in ['medium risk', 'medium-risk']):
        mask &= df['risk_band'] == 'Medium'
        filtered = True
    elif any(w in q for w in ['low risk', 'low-risk']):
        mask &= df['risk_band'] == 'Low'
        filtered = True

    dept_matched = False
    for dept in df['department'].dropna().unique():
        words = [w for w in dept.lower().split() if len(w) > 3]
        if words and words[0] in q:
            mask &= df['department'].str.lower() == dept.lower()
            filtered = True
            dept_matched = True
            break

    if not dept_matched:
        for loc in sorted(df['location'].dropna().unique(), key=len, reverse=True):
            if loc.lower() in q and len(loc) > 2:
                mask &= df['location'].str.lower() == loc.lower()
                filtered = True
                break

    if not filtered:
        return None
    ids = df[mask]['userId'].astype(str).tolist()
    return ids if ids else None


def _pandas_top(query: str, candidate_ids: list[str] | None, n: int = 5) -> list[str]:
    """Return top-N employee docs sorted by most relevant factor for the query."""
    q = query.lower()
    sub = df if not candidate_ids else df[df['userId'].astype(str).isin(candidate_ids)]
    if sub.empty:
        return []
    sort_col = 'risk_score'
    for kw, col in _FACTOR_WORDS.items():
        if kw in q and col in sub.columns:
            sort_col = col
            break
    ascending = sort_col == 'perf_rating'
    top = sub.nsmallest(n, sort_col) if ascending else sub.nlargest(n, sort_col)
    return [_build_doc(r.to_dict()) for _, r in top.iterrows()]


# ── claude CLI helper ──────────────────────────────────────────────────────────
_CLAUDE_BIN = '/Users/I555511/.vscode/extensions/anthropic.claude-code-2.1.267-darwin-arm64/resources/native-binary/claude'

def _ask_claude(prompt: str) -> str:
    result = subprocess.run(
        [_CLAUDE_BIN, '-p', prompt],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or 'claude CLI error')
    return result.stdout.strip()


# ── Tool-calling engine ────────────────────────────────────────────────────────
_TOOL_DESCRIPTIONS = """
You have access to three tools for precise data computation. Use them when the question
requires an exact number (average, count, sum, filter across all employees) that cannot
be reliably estimated from the retrieved profiles alone.

To call a tool, output ONLY a JSON object on a single line (nothing else before or after):

{"tool":"aggregate","column":"<col>","metric":"mean|median|sum|count","group_by":"<col or null>","filter":"<pandas query string or null>"}
{"tool":"query","filter":"<pandas query string>","limit":10}
{"tool":"get_employee","user_id":"<userId>"}
{"tool":"actions_summary"}

Available columns: userId, firstName, lastName, department, division, location,
  risk_score, risk_band, tenure_years, tenure_in_role_years, perf_rating,
  base_salary, compa_ratio, pay_group_compa_ratio, absence_count, sick_count,
  pto_balance_days, bonus_events, has_bonus, bonus_target, mgr_change_count,
  title_change_count, in_calibration, open_reqs_in_dept, internal_app_count,
  is_mentor, is_mentee, in_mentoring, age_years, fte

Use pandas query syntax for filters, e.g.: risk_band == 'High' and department == 'Engineering'
If the question can be answered from the retrieved profiles, answer directly — don't call a tool.
"""

def _run_tool(call: dict) -> str:
    tool = call.get('tool')
    try:
        if tool == 'aggregate':
            col      = call['column']
            metric   = call.get('metric', 'mean')
            group_by = call.get('group_by')
            flt      = call.get('filter')
            sub = df.query(flt) if flt else df
            if sub.empty:
                return 'No employees match that filter.'
            if group_by:
                result = getattr(sub.groupby(group_by)[col], metric)()
                return result.sort_values(ascending=False).head(20).to_string()
            val = getattr(sub[col], metric)()
            return f"{metric}({col}) = {val:.2f} (n={len(sub)})"

        elif tool == 'query':
            flt   = call.get('filter', '')
            limit = int(call.get('limit', 10))
            sub   = df.query(flt) if flt else df
            sub   = sub.head(limit)
            return "\n\n".join(_build_doc(r.to_dict()) for _, r in sub.iterrows())

        elif tool == 'get_employee':
            uid  = str(call.get('user_id', ''))
            rows = df[df['userId'].astype(str) == uid]
            if rows.empty:
                return f'No employee found with userId={uid}'
            return _build_doc(rows.iloc[0].to_dict())

        elif tool == 'actions_summary':
            con = _db()
            rows = con.execute(
                "SELECT user_id, action_type, status, created_at FROM retention_actions ORDER BY created_at DESC"
            ).fetchall()
            con.close()
            if not rows:
                return 'No retention actions have been logged yet.'
            actioned_ids = set(r['user_id'] for r in rows)
            high_risk_ids = set(df[df['risk_band'] == 'High']['userId'].astype(str).tolist())
            no_action = high_risk_ids - actioned_ids
            lines = [f"Total actions logged: {len(rows)}"]
            lines.append(f"High-risk employees with action: {len(high_risk_ids & actioned_ids)}")
            lines.append(f"High-risk employees WITHOUT action: {len(no_action)}")
            for r in rows[:20]:
                lines.append(f"  {r['user_id']} | {r['action_type']} | {r['status']} | {r['created_at']}")
            return "\n".join(lines)

        else:
            return f'Unknown tool: {tool}'
    except Exception as e:
        return f'Tool error: {e}'


def _try_parse_tool(text: str) -> dict | None:
    """Return parsed tool call dict if the response looks like one, else None."""
    stripped = text.strip()
    if not stripped.startswith('{'):
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        m = re.search(r'\{[^{}]+\}', stripped)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return None


def _chat_with_tools(system: str, context: str, history_text: str, message: str) -> str:
    """Two-pass tool-calling loop using the claude CLI."""
    pass1_prompt = (
        f"{system}\n\n{_TOOL_DESCRIPTIONS}\n\n{context}{history_text}\n"
        f"User: {message}\n\nAnswer (or JSON tool call):"
    )
    raw = _ask_claude(pass1_prompt)

    tool_call = _try_parse_tool(raw)
    if tool_call and 'tool' in tool_call:
        tool_result = _run_tool(tool_call)
        pass2_prompt = (
            f"{system}\n\n{context}{history_text}\n"
            f"User: {message}\n\n"
            f"[Tool '{tool_call['tool']}' returned:]\n{tool_result}\n\n"
            f"Now answer the user's question using both the retrieved profiles and the tool result:"
        )
        return _ask_claude(pass2_prompt)

    return raw


_SYSTEM = f"""You are an HR analytics assistant with access to attrition risk data for {summary['total_active_employees']} active employees sourced from SAP SuccessFactors and Payroll.

Current risk distribution: {summary['risk_bands']['high']} High-risk (score > 60) | {summary['risk_bands']['medium']} Medium-risk (31–60) | {summary['risk_bands']['low']} Low-risk (≤ 30). Average risk score: {summary['avg_risk_score']}.

The 17 scored risk factors and weights:
  Role stagnation 20% | Low/missing performance 20% | Below-market pay / compa-ratio < 0.9 12% | Stale compensation 8% | High absence frequency 8% | Unmet bonus target 7% | No bonus history 6% | High PTO balance 5% | Early career age < 35 3% | Pay group compa-ratio 2% | Short tenure 0–2 yr 2% | Manager instability 2% | Not calibrated 2% | Part-time status 2% | Open reqs in dept 1% | Internal job applications 0% (signal only) | No raise since hire 0% (signal only)

When answering:
- Be specific — cite employee names, IDs, and exact data points from the profiles provided
- For lists or comparisons use structured formatting
- Recommend concrete HR retention actions when relevant (e.g. compensation review, career path discussion, calibration inclusion)
- If a question spans many employees, summarise patterns across the retrieved set rather than listing every individual

TOP 10 HIGHEST-RISK EMPLOYEES (always available for context):
{TOP_RISK_CONTEXT}"""


# ── refresh state ─────────────────────────────────────────────────────────────
_refresh_state = {'status': 'idle', 'message': '', 'started_at': None, 'finished_at': None}
_refresh_lock = threading.Lock()


def _csv_age_seconds() -> float:
    try:
        mtime = CSV_PATH.stat().st_mtime
        return time.time() - mtime
    except FileNotFoundError:
        return float('inf')


def _build_index(df_new: 'pd.DataFrame') -> None:
    """Rebuild the global ChromaDB collection from a dataframe."""
    global collection, TOP_RISK_CONTEXT, _SYSTEM
    _chroma2 = chromadb.Client()
    new_col = _chroma2.create_collection('employees')
    batch_docs, batch_ids, batch_metas = [], [], []
    seen: set[str] = set()
    for i, (_, row) in enumerate(df_new.iterrows()):
        rd = row.to_dict()
        doc = _build_doc(rd)
        uid = str(rd.get('userId', ''))
        cid = uid if uid not in seen else f'{uid}_{i}'
        seen.add(cid)
        rsv = rd.get('risk_score', 0.0)
        if isinstance(rsv, float) and math.isnan(rsv):
            rsv = 0.0
        batch_docs.append(doc)
        batch_ids.append(cid)
        batch_metas.append({
            'user_id': uid,
            'department': str(rd.get('department', '') or ''),
            'division': str(rd.get('division', '') or ''),
            'location': str(rd.get('location', '') or ''),
            'risk_band': str(rd.get('risk_band', '') or ''),
            'risk_score': float(rsv),
        })
        if len(batch_docs) >= 100:
            new_col.upsert(documents=batch_docs, ids=batch_ids, metadatas=batch_metas)
            batch_docs, batch_ids, batch_metas = [], [], []
    if batch_docs:
        new_col.upsert(documents=batch_docs, ids=batch_ids, metadatas=batch_metas)
    collection = new_col
    top10 = df_new.nlargest(10, 'risk_score')
    TOP_RISK_CONTEXT = '\n\n'.join(_build_doc(r.to_dict()) for _, r in top10.iterrows())
    # Rebuild BM25 index
    global _all_docs, _all_uids, _bm25, df
    df = df_new
    _all_docs = [_build_doc(r.to_dict()) for _, r in df_new.iterrows()]
    _all_uids = df_new['userId'].astype(str).tolist()
    _bm25 = BM25Okapi([d.lower().split() for d in _all_docs])
    print(f'Index rebuilt: {collection.count()} employees')


def _do_refresh() -> None:
    """Background thread: fetch from SAP → re-score → rebuild index."""
    with _refresh_lock:
        _refresh_state.update({'status': 'running', 'message': 'Fetching live data from SAP SuccessFactors…', 'started_at': datetime.now(timezone.utc).isoformat(), 'finished_at': None})

    try:
        # Step 1: fetch live data via Playwright tests
        _refresh_state['message'] = 'Step 1/3 — Fetching from SAP (this takes ~2 min)…'
        r1 = subprocess.run(
            ['npm', 'run', 'fetch:all'],
            cwd=ROOT, capture_output=True, text=True, timeout=600
        )
        if r1.returncode != 0:
            raise RuntimeError(f'fetch:all failed: {r1.stderr[-500:]}')

        # Step 2: re-score employees
        _refresh_state['message'] = 'Step 2/3 — Re-scoring employees…'
        r2 = subprocess.run(
            ['python3', 'model/attrition_enriched.py'],
            cwd=ROOT, capture_output=True, text=True, timeout=120
        )
        if r2.returncode != 0:
            raise RuntimeError(f'model failed: {r2.stderr[-500:]}')

        # Step 3: rebuild ChromaDB index
        _refresh_state['message'] = 'Step 3/3 — Rebuilding search index…'
        df_new = pd.read_csv(CSV_PATH)
        _build_index(df_new)

        # Rebuild dashboard HTML too
        subprocess.run(['python3', 'model/build_dashboard.py'], cwd=ROOT, timeout=60)

        _refresh_state.update({
            'status': 'done',
            'message': f'Refresh complete — {len(df_new)} employees re-indexed from live SAP data.',
            'finished_at': datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        _refresh_state.update({
            'status': 'error',
            'message': str(e),
            'finished_at': datetime.now(timezone.utc).isoformat(),
        })

# ── routes ─────────────────────────────────────────────────────────────────────
@app.route('/health')
def health():
    age = _csv_age_seconds()
    age_str = f'{int(age//3600)}h ago' if age > 3600 else f'{int(age//60)}m ago' if age > 60 else 'just now'
    return jsonify({'status': 'ok', 'indexed': collection.count(), 'data_age': age_str})


@app.route('/refresh', methods=['POST'])
def refresh():
    if _refresh_state['status'] == 'running':
        return jsonify({'error': 'Refresh already in progress', 'state': _refresh_state}), 409
    t = threading.Thread(target=_do_refresh, daemon=True)
    t.start()
    return jsonify({'status': 'started', 'message': 'Live SAP data fetch started in background'})


@app.route('/refresh/status')
def refresh_status():
    age = _csv_age_seconds()
    age_str = f'{int(age//3600)}h ago' if age > 3600 else f'{int(age//60)}m ago' if age > 60 else 'just now'
    return jsonify({**_refresh_state, 'data_age': age_str})


@app.route('/chat', methods=['POST'])
def chat():
    data = request.get_json(force=True)
    message = (data.get('message') or '').strip()
    history = data.get('history') or []

    if not message:
        return jsonify({'error': 'message is required'}), 400

    q_lower = message.lower()

    # ── 1. Metadata filter (department / location / risk band) ─────────────────
    candidate_ids = _extract_candidate_ids(message)
    where = {'user_id': {'$in': candidate_ids}} if candidate_ids else None
    n_sem = min(15, len(candidate_ids)) if candidate_ids else 15

    # ── 2. Semantic search (ChromaDB) ──────────────────────────────────────────
    sem_results = collection.query(query_texts=[message], n_results=n_sem, where=where)
    sem_docs  = sem_results['documents'][0] if sem_results['documents'] else []
    sem_metas = sem_results['metadatas'][0]  if sem_results['metadatas'] else []
    seen_ids  = {m.get('user_id', '') for m in sem_metas}

    # ── 3. BM25 keyword search ─────────────────────────────────────────────────
    bm25_scores = _bm25.get_scores(q_lower.split())
    top_bm25_idx = np.argsort(bm25_scores)[::-1][:10]
    bm25_docs = []
    for idx in top_bm25_idx:
        if bm25_scores[idx] > 0 and _all_uids[idx] not in seen_ids:
            uid = _all_uids[idx]
            if candidate_ids is None or uid in candidate_ids:
                bm25_docs.append(_all_docs[idx])
                seen_ids.add(uid)

    # ── 4. Pandas top-N boost for ranking queries ──────────────────────────────
    rank_docs = []
    if any(w in q_lower for w in _RANK_WORDS):
        for doc in _pandas_top(message, candidate_ids, n=5):
            uid = doc.split('(')[1].split(')')[0] if '(' in doc else ''
            if uid not in seen_ids:
                rank_docs.append(doc)
                seen_ids.add(uid)

    # ── 5. Merge: rank boost first, then semantic, then BM25 ──────────────────
    retrieved_docs = (rank_docs + sem_docs + bm25_docs)[:20]
    retrieval_note = f"Filters: {candidate_ids and 'metadata+' or ''}semantic+BM25 | Candidates: {len(candidate_ids) if candidate_ids else 'all'} | Retrieved: {len(retrieved_docs)}"

    context = (
        f"=== RETRIEVED EMPLOYEE PROFILES ({retrieval_note}) ===\n\n"
        + "\n\n".join(retrieved_docs)
    )

    # Build conversation history text
    history_text = ''
    for t in history:
        role = 'User' if t['role'] == 'user' else 'Assistant'
        history_text += f"\n{role}: {t['content']}\n"

    try:
        answer = _chat_with_tools(_SYSTEM, context, history_text, message)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    sources = [m.get('user_id', '') for m in sem_metas[:5]]

    return jsonify({'response': answer, 'sources': sources})


# ── Action tracking routes ─────────────────────────────────────────────────────
@app.route('/actions', methods=['GET'])
def list_actions():
    user_id = request.args.get('user_id')
    status  = request.args.get('status')
    con = _db()
    q, params = "SELECT * FROM retention_actions WHERE 1=1", []
    if user_id:
        q += " AND user_id = ?"; params.append(user_id)
    if status:
        q += " AND status = ?";  params.append(status)
    q += " ORDER BY created_at DESC"
    rows = [dict(r) for r in con.execute(q, params).fetchall()]
    con.close()
    return jsonify(rows)


@app.route('/actions', methods=['POST'])
def create_action():
    data = request.get_json(force=True)
    required = ('user_id', 'action_type')
    if not all(data.get(k) for k in required):
        return jsonify({'error': 'user_id and action_type are required'}), 400
    con = _db()
    cur = con.execute(
        "INSERT INTO retention_actions (user_id, action_type, description, owner, status) VALUES (?,?,?,?,?)",
        (data['user_id'], data['action_type'], data.get('description',''), data.get('owner',''), data.get('status','open'))
    )
    con.commit()
    row = dict(con.execute("SELECT * FROM retention_actions WHERE id=?", (cur.lastrowid,)).fetchone())
    con.close()
    return jsonify(row), 201


@app.route('/actions/<int:action_id>', methods=['PATCH'])
def update_action(action_id):
    data = request.get_json(force=True)
    allowed = {'action_type', 'description', 'owner', 'status'}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({'error': 'Nothing to update'}), 400
    set_clause = ', '.join(f"{k}=?" for k in updates)
    set_clause += ', updated_at=CURRENT_TIMESTAMP'
    con = _db()
    con.execute(f"UPDATE retention_actions SET {set_clause} WHERE id=?",
                [*updates.values(), action_id])
    con.commit()
    row = con.execute("SELECT * FROM retention_actions WHERE id=?", (action_id,)).fetchone()
    con.close()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(dict(row))


@app.route('/actions/<int:action_id>', methods=['DELETE'])
def delete_action(action_id):
    con = _db()
    con.execute("DELETE FROM retention_actions WHERE id=?", (action_id,))
    con.commit()
    con.close()
    return '', 204


if __name__ == '__main__':
    print("RAG server ready at http://localhost:5001")
    app.run(host='0.0.0.0', port=5001, debug=False)
