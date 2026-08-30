import { test } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { SFSF_BASE, SFSF_COMPANY, SFSF_USERNAME, SFSF_PASSWORD, PY_BASE, PY_COMPANY, PY_USERNAME, PY_PASSWORD } from './config';

const SFSF_FIX  = path.resolve(__dirname, '../fixtures/sfsf');
const PY_FIX    = path.resolve(__dirname, '../fixtures/payroll');

test.use({ actionTimeout: 90000, navigationTimeout: 90000 });

async function odata(page: any, fixtures: string, name: string, url: string) {
  const res = await page.evaluate(async (u: string) => {
    const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
    return { status: r.status, body: await r.text() };
  }, url);
  fs.writeFileSync(path.join(fixtures, `${name}.json`), res.body);
  let count = 0;
  try { count = JSON.parse(res.body).d?.results?.length ?? 0; } catch {}
  console.log(`  ${name}: HTTP ${res.status}, ${res.body.length} bytes, ${count} records`);
  return res;
}

test('Fetch manager and org change history', async ({ page }) => {
  // ── SFSF ──────────────────────────────────────────────────────────────────
  console.log('\n══ SFSF login ══');
  await page.goto(`${SFSF_BASE}/login?company=${SFSF_COMPANY}`);
  await page.waitForLoadState('networkidle');
  await page.locator('input[type="text"]').first().fill('sfadmin');
  await page.locator('input[type="password"]').first().fill('Demo2026!');
  await page.locator('button[type="submit"], input[type="submit"]').first().click();
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(3000);
  console.log('Logged in:', page.url());

  // Probe EmpJob fields — we need dept, managerId, jobTitle, costCenter per event
  const ejProbe = await page.evaluate(async (u: string) => {
    const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
    return { status: r.status, body: await r.text() };
  }, `${SFSF_BASE}/odata/v2/EmpJob?$format=json&$top=1`);
  console.log('\nEmpJob all fields:');
  try {
    const j = JSON.parse(ejProbe.body);
    const rec = j.d?.results?.[0];
    if (rec) console.log(Object.keys(rec).filter(k => k !== '__metadata').join(', '));
  } catch {}

  // Full EmpJob history — all events for all employees
  // Pull 2000 to get multiple records per employee
  await odata(page, SFSF_FIX, 'emp-job-full',
    `${SFSF_BASE}/odata/v2/EmpJob?$format=json&$top=2000` +
    `&$select=userId,startDate,endDate,seqNumber,jobTitle,position,managerId,` +
    `department,division,location,costCenter,businessUnit,emplStatus,` +
    `jobCode,companyEntryDate,originalStartDate`);

  // EmpJobRelationship — matrix/dotted-line manager history
  const ejrProbe = await page.evaluate(async (u: string) => {
    const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
    return { status: r.status, body: await r.text() };
  }, `${SFSF_BASE}/odata/v2/EmpJobRelationship?$format=json&$top=1`);
  console.log('\nEmpJobRelationship probe:', ejrProbe.status);
  if (ejrProbe.status === 200) {
    try {
      const j = JSON.parse(ejrProbe.body);
      const rec = j.d?.results?.[0];
      if (rec) console.log('Fields:', Object.keys(rec).filter(k => k !== '__metadata').join(', '));
    } catch {}
    await odata(page, SFSF_FIX, 'emp-job-relationship',
      `${SFSF_BASE}/odata/v2/EmpJobRelationship?$format=json&$top=2000`);
  }

  // EmpGlobalAssignment — already partially fetched, get full
  await odata(page, SFSF_FIX, 'emp-global-assignment',
    `${SFSF_BASE}/odata/v2/EmpGlobalAssignment?$format=json&$top=500`);

  // ── Payroll ───────────────────────────────────────────────────────────────
  console.log('\n══ Payroll login ══');
  await page.goto(`${PY_BASE}/sf/home?bplte_company=${PY_COMPANY}`);
  await page.waitForLoadState('networkidle');
  await page.locator('input[type="text"]').first().fill('103200');
  await page.locator('input[type="password"]').first().fill('PYdemo@2024');
  await page.locator('button[type="submit"], input[type="submit"]').first().click();
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(3000);
  console.log('Logged in:', page.url());

  // EmpJob full history from payroll instance
  const ejPyProbe = await page.evaluate(async (u: string) => {
    const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
    return { status: r.status, body: await r.text() };
  }, `${PY_BASE}/odata/v2/EmpJob?$format=json&$top=1`);
  console.log('\nPayroll EmpJob fields:');
  try {
    const j = JSON.parse(ejPyProbe.body);
    const rec = j.d?.results?.[0];
    if (rec) console.log(Object.keys(rec).filter(k => k !== '__metadata').join(', '));
  } catch {}

  await odata(page, PY_FIX, 'py-emp-job-full',
    `${PY_BASE}/odata/v2/EmpJob?$format=json&$top=2000` +
    `&$select=userId,startDate,endDate,seqNumber,jobTitle,position,managerId,` +
    `department,division,location,costCenter,businessUnit,emplStatus,jobCode`);

  console.log('\n✅ Manager/org change data fetched.');
});
