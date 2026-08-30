import { test } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { SFSF_BASE, SFSF_COMPANY, SFSF_USERNAME, SFSF_PASSWORD, PY_BASE, PY_COMPANY, PY_USERNAME, PY_PASSWORD } from './config';

const SFSF_FIXTURES = path.resolve(__dirname, '../fixtures/sfsf');
const PY_FIXTURES   = path.resolve(__dirname, '../fixtures/payroll');

function ensureDir(d: string) { if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true }); }

async function odata(page: any, fixtures: string, name: string, url: string) {
  const res = await page.evaluate(async (u: string) => {
    const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
    return { status: r.status, body: await r.text() };
  }, url);
  fs.writeFileSync(path.join(fixtures, `${name}.json`), res.body);
  const bytes = res.body.length;
  let count = 0;
  try { count = JSON.parse(res.body).d?.results?.length ?? 0; } catch {}
  console.log(`  ${name}: HTTP ${res.status}, ${bytes} bytes, ${count} records`);
  return res;
}

test.use({ actionTimeout: 60000, navigationTimeout: 90000 });

test('Fetch additional attrition signals from both instances', async ({ page }) => {
  ensureDir(SFSF_FIXTURES);
  ensureDir(PY_FIXTURES);

  // ─── SFSF instance ────────────────────────────────────────────────────────
  console.log('\n══ SFSF (SFSALES010044) ══');
  await page.goto(`${SFSF_BASE}/login?company=${SFSF_COMPANY}`);
  await page.waitForLoadState('networkidle');
  await page.locator('input[type="text"]').first().fill('sfadmin');
  await page.locator('input[type="password"]').first().fill('Demo2026!');
  await page.locator('button[type="submit"], input[type="submit"]').first().click();
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(3000);
  console.log('Logged in:', page.url());

  // First, discover field names for EmployeeTime
  const etDiscover = await page.evaluate(async (u: string) => {
    const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
    return { status: r.status, body: await r.text() };
  }, `${SFSF_BASE}/odata/v2/EmployeeTime?$format=json&$top=1`);
  console.log('\nEmployeeTime sample fields:');
  try {
    const j = JSON.parse(etDiscover.body);
    const rec = j.d?.results?.[0];
    if (rec) console.log(Object.keys(rec).filter(k => k !== '__metadata').join(', '));
  } catch {}

  // Discover JobApplication fields
  const jaDiscover = await page.evaluate(async (u: string) => {
    const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
    return { status: r.status, body: await r.text() };
  }, `${SFSF_BASE}/odata/v2/JobApplication?$format=json&$top=1`);
  console.log('\nJobApplication sample fields:');
  try {
    const j = JSON.parse(jaDiscover.body);
    const rec = j.d?.results?.[0];
    if (rec) console.log(Object.keys(rec).filter(k => k !== '__metadata').join(', '));
  } catch {}

  // Discover CompetencyRating fields
  const crDiscover = await page.evaluate(async (u: string) => {
    const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
    return { status: r.status, body: await r.text() };
  }, `${SFSF_BASE}/odata/v2/CompetencyRating?$format=json&$top=1`);
  console.log('\nCompetencyRating sample fields:');
  let crFields: string[] = [];
  try {
    const j = JSON.parse(crDiscover.body);
    const rec = j.d?.results?.[0];
    if (rec) { crFields = Object.keys(rec).filter(k => k !== '__metadata'); console.log(crFields.join(', ')); }
  } catch {}

  // Discover TalentPool fields
  const tpDiscover = await page.evaluate(async (u: string) => {
    const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
    return { status: r.status, body: await r.text() };
  }, `${SFSF_BASE}/odata/v2/TalentPool?$format=json&$top=3`);
  console.log('\nTalentPool sample fields:');
  try {
    const j = JSON.parse(tpDiscover.body);
    const rec = j.d?.results?.[0];
    if (rec) console.log(Object.keys(rec).filter(k => k !== '__metadata').join(', '));
    console.log('Records:', j.d?.results?.length);
  } catch {}

  console.log('\nFetching full datasets from SFSF...');

  // EmployeeTime — all records (absence history)
  await odata(page, SFSF_FIXTURES, 'employee-time',
    `${SFSF_BASE}/odata/v2/EmployeeTime?$format=json&$top=2000`);

  // JobApplication — internal job applications
  await odata(page, SFSF_FIXTURES, 'job-applications',
    `${SFSF_BASE}/odata/v2/JobApplication?$format=json&$top=2000`);

  // CompetencyRating
  await odata(page, SFSF_FIXTURES, 'competency-ratings',
    `${SFSF_BASE}/odata/v2/CompetencyRating?$format=json&$top=2000`);

  // TalentPool members — need to expand to get userId
  await odata(page, SFSF_FIXTURES, 'talent-pool',
    `${SFSF_BASE}/odata/v2/TalentPool?$format=json&$top=500`);

  // TalentPoolMembership — to link employees to pools
  const tpmRes = await page.evaluate(async (u: string) => {
    const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
    return { status: r.status, body: await r.text() };
  }, `${SFSF_BASE}/odata/v2/TalentPoolMembership?$format=json&$top=1`);
  console.log('\nTalentPoolMembership probe:', tpmRes.status);
  try {
    const j = JSON.parse(tpmRes.body);
    const rec = j.d?.results?.[0];
    if (rec) console.log('Fields:', Object.keys(rec).filter(k => k !== '__metadata').join(', '));
  } catch {}
  if (tpmRes.status === 200) {
    await odata(page, SFSF_FIXTURES, 'talent-pool-membership',
      `${SFSF_BASE}/odata/v2/TalentPoolMembership?$format=json&$top=2000`);
  } else {
    fs.writeFileSync(path.join(SFSF_FIXTURES, 'talent-pool-membership.json'), '{"d":{"results":[]}}');
  }

  // ─── Payroll instance ─────────────────────────────────────────────────────
  console.log('\n══ Payroll (SFSALES009656) ══');
  await page.goto(`${PY_BASE}/sf/home?bplte_company=${PY_COMPANY}`);
  await page.waitForLoadState('networkidle');
  await page.locator('input[type="text"]').first().fill('103200');
  await page.locator('input[type="password"]').first().fill('PYdemo@2024');
  await page.locator('button[type="submit"], input[type="submit"]').first().click();
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(3000);
  console.log('Logged in:', page.url());

  // EmployeeTime from payroll instance too
  await odata(page, PY_FIXTURES, 'py-employee-time',
    `${PY_BASE}/odata/v2/EmployeeTime?$format=json&$top=2000`);

  // Probe PerPersonal for marital status / family situation
  const ppDiscover = await page.evaluate(async (u: string) => {
    const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
    return { status: r.status, body: await r.text() };
  }, `${PY_BASE}/odata/v2/PerPersonal?$format=json&$top=1`);
  console.log('\nPerPersonal fields:');
  try {
    const j = JSON.parse(ppDiscover.body);
    const rec = j.d?.results?.[0];
    if (rec) console.log(Object.keys(rec).filter(k => k !== '__metadata').join(', '));
  } catch {}

  if (JSON.parse(ppDiscover.body).d?.results?.[0]) {
    await odata(page, PY_FIXTURES, 'py-per-personal',
      `${PY_BASE}/odata/v2/PerPersonal?$format=json&$top=500`);
  }

  console.log('\n✅ All additional signal data fetched.');
});
