import { test } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { SFSF_BASE, SFSF_COMPANY, SFSF_USERNAME, SFSF_PASSWORD } from './config';

const FIXTURES = path.resolve(__dirname, '../fixtures/sfsf');
function ensureDir(d: string) { if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true }); }

async function odata(page: any, name: string, url: string) {
  const res = await page.evaluate(async (u: string) => {
    const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
    return { status: r.status, body: await r.text() };
  }, url);
  fs.writeFileSync(path.join(FIXTURES, `${name}.json`), res.body);
  let count = 0;
  try { count = JSON.parse(res.body).d?.results?.length ?? 0; } catch {}
  console.log(`  ${name}: HTTP ${res.status}, ${res.body.length} bytes, ${count} records`);
  return { status: res.status, count };
}

async function probe(page: any, entity: string) {
  const url = `${SFSF_BASE}/odata/v2/${entity}?$format=json&$top=1`;
  const res = await page.evaluate(async (u: string) => {
    const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
    return { status: r.status, body: await r.text() };
  }, url);
  let fields: string[] = [];
  let hasData = false;
  try {
    const j = JSON.parse(res.body);
    const rec = j.d?.results?.[0];
    if (rec) { fields = Object.keys(rec).filter(k => k !== '__metadata'); hasData = true; }
  } catch {}
  const preview = fields.slice(0, 10).join(', ');
  console.log(`  [${res.status}${hasData ? ' ✓' : ' ✗'}] ${entity}: ${preview || '(no data)'}`);
  return hasData;
}

test.use({ actionTimeout: 60000, navigationTimeout: 90000 });

test('Fetch all SF report data: Goals, Pay, Demographics, Position, Calibration, Succession', async ({ page }) => {
  ensureDir(FIXTURES);

  console.log('\n══ SF Extended Reports (SFSALES010044) ══');
  await page.goto(`${SFSF_BASE}/login?company=${SFSF_COMPANY}`);
  await page.waitForLoadState('networkidle');
  await page.locator('input[type="text"]').first().fill(SFSF_USERNAME);
  await page.locator('input[type="password"]').first().fill(SFSF_PASSWORD);
  await page.locator('button[type="submit"], input[type="submit"]').first().click();
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(3000);
  console.log('Logged in:', page.url());

  // ── Probing all target entities ──────────────────────────────────────────
  console.log('\n── Probing entities ──');
  const [
    hasGoalV2, hasGoal, hasDevGoal, hasDevPlan,
    hasEmpComp, hasCalibSubj, hasCalibSess, hasSuccession,
    hasPerPersonal, hasPerPerson, hasPosition,
    hasEmpPayRecurring, hasEmpPayNonRecurring, hasPayGroup,
    hasPayScaleArea, hasPayScaleType, hasWorkSchedule,
    hasJobReq, hasReferral, hasEmpBenefit,
  ] = await Promise.all([
    probe(page, 'GoalPlanV2'),
    probe(page, 'Goal'),
    probe(page, 'DevelopmentGoal'),
    probe(page, 'DevGoalPlan'),
    probe(page, 'EmpCompensation'),
    probe(page, 'CalibrationSubjectRating'),
    probe(page, 'CalibrationSession'),
    probe(page, 'SuccessionPlanItem'),
    probe(page, 'PerPersonal'),
    probe(page, 'PerPerson'),
    probe(page, 'Position'),
    probe(page, 'EmpPayCompRecurring'),
    probe(page, 'EmpPayCompNonRecurring'),
    probe(page, 'PayGroup'),
    probe(page, 'PayScaleArea'),
    probe(page, 'PayScaleType'),
    probe(page, 'WorkSchedule'),
    probe(page, 'JobRequisition'),
    probe(page, 'Referral'),
    probe(page, 'EmpBenefit'),
  ]);

  // ── 1. Goals (Extract Goal Data) ─────────────────────────────────────────
  console.log('\n── Goals ──');
  if (hasGoalV2) {
    await odata(page, 'goal-plan-v2', `${SFSF_BASE}/odata/v2/GoalPlanV2?$format=json&$top=2000`);
  } else if (hasGoal) {
    await odata(page, 'goals', `${SFSF_BASE}/odata/v2/Goal?$format=json&$top=2000`);
  } else {
    fs.writeFileSync(path.join(FIXTURES, 'goals.json'), '{"d":{"results":[]}}');
    console.log('  goals: not accessible');
  }

  // ── 2. Development Goals (Extract Development Goal Data) ─────────────────
  console.log('\n── Development Goals ──');
  if (hasDevGoal) {
    await odata(page, 'dev-goals', `${SFSF_BASE}/odata/v2/DevelopmentGoal?$format=json&$top=2000`);
  } else if (hasDevPlan) {
    await odata(page, 'dev-goal-plan', `${SFSF_BASE}/odata/v2/DevGoalPlan?$format=json&$top=2000`);
  } else {
    fs.writeFileSync(path.join(FIXTURES, 'dev-goals.json'), '{"d":{"results":[]}}');
    console.log('  dev-goals: not accessible');
  }

  // ── 3. Employee Demographics (EE DoB, Gender, Nationality) ───────────────
  console.log('\n── Employee Demographics ──');
  if (hasPerPersonal) {
    await odata(page, 'per-personal',
      `${SFSF_BASE}/odata/v2/PerPersonal?$format=json&$top=1000`);
  } else {
    fs.writeFileSync(path.join(FIXTURES, 'per-personal.json'), '{"d":{"results":[]}}');
    console.log('  per-personal: not accessible');
  }
  if (hasPerPerson) {
    await odata(page, 'per-person',
      `${SFSF_BASE}/odata/v2/PerPerson?$format=json&$top=1000`);
  } else {
    fs.writeFileSync(path.join(FIXTURES, 'per-person.json'), '{"d":{"results":[]}}');
  }

  // ── 4. Compa Ratio / Employee Compensation ────────────────────────────────
  console.log('\n── Compa Ratios / Compensation ──');
  if (hasEmpComp) {
    await odata(page, 'emp-compensation',
      `${SFSF_BASE}/odata/v2/EmpCompensation?$format=json&$top=1000`);
  } else {
    fs.writeFileSync(path.join(FIXTURES, 'emp-compensation.json'), '{"d":{"results":[]}}');
    console.log('  emp-compensation: not accessible (will use payroll compa-ratio)');
  }

  // ── 5. All Pay Components by Employee (Recurring Comp) ───────────────────
  console.log('\n── Pay Components ──');
  if (hasEmpPayRecurring) {
    await odata(page, 'emp-pay-recurring',
      `${SFSF_BASE}/odata/v2/EmpPayCompRecurring?$format=json&$top=2000`);
  } else {
    fs.writeFileSync(path.join(FIXTURES, 'emp-pay-recurring.json'), '{"d":{"results":[]}}');
    console.log('  emp-pay-recurring: not accessible');
  }
  if (hasEmpPayNonRecurring) {
    await odata(page, 'emp-pay-nonrecurring',
      `${SFSF_BASE}/odata/v2/EmpPayCompNonRecurring?$format=json&$top=2000`);
  } else {
    fs.writeFileSync(path.join(FIXTURES, 'emp-pay-nonrecurring.json'), '{"d":{"results":[]}}');
    console.log('  emp-pay-nonrecurring: not accessible');
  }

  // ── 6. Job - Position Info ────────────────────────────────────────────────
  console.log('\n── Position Info ──');
  if (hasPosition) {
    await odata(page, 'position',
      `${SFSF_BASE}/odata/v2/Position?$format=json&$top=1000`);
  } else {
    fs.writeFileSync(path.join(FIXTURES, 'position.json'), '{"d":{"results":[]}}');
    console.log('  position: not accessible');
  }
  // Pay Grade from EmpJob
  await odata(page, 'emp-job-grade',
    `${SFSF_BASE}/odata/v2/EmpJob?$format=json&$top=1000`);

  // ── 7. Pay Group ──────────────────────────────────────────────────────────
  console.log('\n── Pay Group / Pay Scale ──');
  if (hasPayGroup) {
    await odata(page, 'pay-group',
      `${SFSF_BASE}/odata/v2/PayGroup?$format=json&$top=500`);
  } else {
    fs.writeFileSync(path.join(FIXTURES, 'pay-group.json'), '{"d":{"results":[]}}');
  }
  if (hasPayScaleArea) {
    await odata(page, 'pay-scale-area',
      `${SFSF_BASE}/odata/v2/PayScaleArea?$format=json&$top=500`);
  } else {
    fs.writeFileSync(path.join(FIXTURES, 'pay-scale-area.json'), '{"d":{"results":[]}}');
  }
  if (hasPayScaleType) {
    await odata(page, 'pay-scale-type',
      `${SFSF_BASE}/odata/v2/PayScaleType?$format=json&$top=500`);
  } else {
    fs.writeFileSync(path.join(FIXTURES, 'pay-scale-type.json'), '{"d":{"results":[]}}');
  }

  // ── 8. Calibration (Calibration Alert) ───────────────────────────────────
  console.log('\n── Calibration ──');
  if (hasCalibSubj) {
    await odata(page, 'calibration-ratings',
      `${SFSF_BASE}/odata/v2/CalibrationSubjectRating?$format=json&$top=1000`);
  } else if (hasCalibSess) {
    await odata(page, 'calibration-sessions',
      `${SFSF_BASE}/odata/v2/CalibrationSession?$format=json&$top=500`);
    // Expand subjectList for each session to get per-employee calibration outcomes
    const sessionIds = ['581', '701', '700', '381', '422', '421', '583', '582', '501', '861'];
    const allSubjects: any[] = [];
    for (const sid of sessionIds) {
      const res = await page.evaluate(async (u: string) => {
        const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
        return { status: r.status, body: await r.text() };
      }, `${SFSF_BASE}/odata/v2/CalibrationSession(${sid}L)/subjectList?$format=json&$top=500`);
      try {
        const j = JSON.parse(res.body);
        const results = j.d?.results || [];
        results.forEach((r: any) => r._sessionId = sid);
        allSubjects.push(...results);
        console.log(`  calibration session ${sid}: ${results.length} subjects`);
      } catch {}
    }
    fs.writeFileSync(path.join(FIXTURES, 'calibration-subjects.json'),
      JSON.stringify({ d: { results: allSubjects } }));
    console.log(`  calibration-subjects total: ${allSubjects.length} records`);
  } else {
    fs.writeFileSync(path.join(FIXTURES, 'calibration-ratings.json'), '{"d":{"results":[]}}');
    fs.writeFileSync(path.join(FIXTURES, 'calibration-subjects.json'), '{"d":{"results":[]}}');
    console.log('  calibration: not accessible');
  }

  // ── 9. Succession Planning ────────────────────────────────────────────────
  console.log('\n── Succession ──');
  if (hasSuccession) {
    await odata(page, 'succession-plan',
      `${SFSF_BASE}/odata/v2/SuccessionPlanItem?$format=json&$top=1000`);
  } else {
    fs.writeFileSync(path.join(FIXTURES, 'succession-plan.json'), '{"d":{"results":[]}}');
    console.log('  succession: not accessible');
  }

  // ── 10. Work Schedule / Time Profile ─────────────────────────────────────
  console.log('\n── Work Schedule / Time Profile ──');
  if (hasWorkSchedule) {
    await odata(page, 'work-schedule',
      `${SFSF_BASE}/odata/v2/WorkSchedule?$format=json&$top=500`);
  } else {
    fs.writeFileSync(path.join(FIXTURES, 'work-schedule.json'), '{"d":{"results":[]}}');
  }

  // ── 11. Hired Candidates / Job Requisitions ───────────────────────────────
  console.log('\n── Recruitment ──');
  if (hasJobReq) {
    await odata(page, 'job-requisitions',
      `${SFSF_BASE}/odata/v2/JobRequisition?$format=json&$top=500`);
  } else {
    fs.writeFileSync(path.join(FIXTURES, 'job-requisitions.json'), '{"d":{"results":[]}}');
  }
  if (hasReferral) {
    await odata(page, 'referrals',
      `${SFSF_BASE}/odata/v2/Referral?$format=json&$top=500`);
  } else {
    fs.writeFileSync(path.join(FIXTURES, 'referrals.json'), '{"d":{"results":[]}}');
  }

  // ── 12. Benefits ──────────────────────────────────────────────────────────
  console.log('\n── Benefits ──');
  if (hasEmpBenefit) {
    await odata(page, 'emp-benefits',
      `${SFSF_BASE}/odata/v2/EmpBenefit?$format=json&$top=1000`);
  } else {
    fs.writeFileSync(path.join(FIXTURES, 'emp-benefits.json'), '{"d":{"results":[]}}');
  }

  console.log('\n✅ All SF report data fetched.');
});
