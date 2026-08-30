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

test('Fetch leave data from both instances', async ({ page }) => {
  // ── SFSF ──────────────────────────────────────────────────────────────
  console.log('\n══ SFSF login ══');
  await page.goto(`${SFSF_BASE}/login?company=${SFSF_COMPANY}`);
  await page.waitForLoadState('networkidle');
  await page.locator('input[type="text"]').first().fill('sfadmin');
  await page.locator('input[type="password"]').first().fill('Demo2026!');
  await page.locator('button[type="submit"], input[type="submit"]').first().click();
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(3000);
  console.log('Logged in:', page.url());

  // EmployeeTime — newest 2000 records, exclude WORK and BREAKSCHED
  // OData v2 doesn't support $filter on all fields, so pull sorted desc and filter client-side
  await odata(page, SFSF_FIX, 'employee-time-recent',
    `${SFSF_BASE}/odata/v2/EmployeeTime?$format=json&$top=2000` +
    `&$orderby=startDate%20desc` +
    `&$select=externalCode,userId,startDate,endDate,quantityInDays,quantityInHours,` +
    `timeType,approvalStatus,absenceDurationCategory,loaExpectedReturnDate,loaActualReturnDate,comment`);

  // TimeAccountDetail — leave balance accruals/deductions per employee
  // First probe to see fields
  const tad = await page.evaluate(async (u: string) => {
    const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
    return { status: r.status, body: await r.text() };
  }, `${SFSF_BASE}/odata/v2/TimeAccountDetail?$format=json&$top=1`);
  console.log('\nTimeAccountDetail probe:', tad.status);
  if (tad.status === 200) {
    try {
      const j = JSON.parse(tad.body);
      const rec = j.d?.results?.[0];
      if (rec) console.log('Fields:', Object.keys(rec).filter(k => k !== '__metadata').join(', '));
    } catch {}
    await odata(page, SFSF_FIX, 'time-account-detail',
      `${SFSF_BASE}/odata/v2/TimeAccountDetail?$format=json&$top=2000`);
  }

  // TimeAccount — leave balance snapshots per employee
  const ta = await page.evaluate(async (u: string) => {
    const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
    return { status: r.status, body: await r.text() };
  }, `${SFSF_BASE}/odata/v2/TimeAccount?$format=json&$top=1`);
  console.log('\nTimeAccount probe:', ta.status);
  if (ta.status === 200) {
    try {
      const j = JSON.parse(ta.body);
      const rec = j.d?.results?.[0];
      if (rec) console.log('Fields:', Object.keys(rec).filter(k => k !== '__metadata').join(', '));
    } catch {}
    await odata(page, SFSF_FIX, 'time-account',
      `${SFSF_BASE}/odata/v2/TimeAccount?$format=json&$top=2000`);
  }

  // TimeAccountPayout — leave payouts (unused leave paid out = lump sum risk indicator)
  const tap = await page.evaluate(async (u: string) => {
    const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
    return { status: r.status, body: await r.text() };
  }, `${SFSF_BASE}/odata/v2/TimeAccountPayout?$format=json&$top=1`);
  console.log('\nTimeAccountPayout probe:', tap.status);
  if (tap.status === 200) {
    try {
      const j = JSON.parse(tap.body);
      const rec = j.d?.results?.[0];
      if (rec) console.log('Fields:', Object.keys(rec).filter(k => k !== '__metadata').join(', '));
    } catch {}
    await odata(page, SFSF_FIX, 'time-account-payout',
      `${SFSF_BASE}/odata/v2/TimeAccountPayout?$format=json&$top=500`);
  }

  // ── Payroll ────────────────────────────────────────────────────────────
  console.log('\n══ Payroll login ══');
  await page.goto(`${PY_BASE}/sf/home?bplte_company=${PY_COMPANY}`);
  await page.waitForLoadState('networkidle');
  await page.locator('input[type="text"]').first().fill('103200');
  await page.locator('input[type="password"]').first().fill('PYdemo@2024');
  await page.locator('button[type="submit"], input[type="submit"]').first().click();
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(3000);
  console.log('Logged in:', page.url());

  // EmployeeTime — recent sorted desc
  await odata(page, PY_FIX, 'py-employee-time-recent',
    `${PY_BASE}/odata/v2/EmployeeTime?$format=json&$top=2000` +
    `&$orderby=startDate%20desc` +
    `&$select=externalCode,userId,startDate,endDate,quantityInDays,quantityInHours,` +
    `timeType,approvalStatus,absenceDurationCategory,loaExpectedReturnDate,loaActualReturnDate`);

  // TimeAccountDetail from payroll
  const tadPy = await page.evaluate(async (u: string) => {
    const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
    return { status: r.status, body: await r.text() };
  }, `${PY_BASE}/odata/v2/TimeAccountDetail?$format=json&$top=1`);
  if (tadPy.status === 200) {
    try {
      const j = JSON.parse(tadPy.body);
      const rec = j.d?.results?.[0];
      if (rec) console.log('Payroll TimeAccountDetail fields:', Object.keys(rec).filter(k => k !== '__metadata').join(', '));
    } catch {}
    await odata(page, PY_FIX, 'py-time-account-detail',
      `${PY_BASE}/odata/v2/TimeAccountDetail?$format=json&$top=2000`);
  }

  // TimeAccount from payroll
  const taPy = await page.evaluate(async (u: string) => {
    const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
    return { status: r.status, body: await r.text() };
  }, `${PY_BASE}/odata/v2/TimeAccount?$format=json&$top=1`);
  if (taPy.status === 200) {
    try {
      const j = JSON.parse(taPy.body);
      const rec = j.d?.results?.[0];
      if (rec) console.log('Payroll TimeAccount fields:', Object.keys(rec).filter(k => k !== '__metadata').join(', '));
    } catch {}
    await odata(page, PY_FIX, 'py-time-account',
      `${PY_BASE}/odata/v2/TimeAccount?$format=json&$top=2000`);
  }

  console.log('\n✅ Leave data fetch complete.');
});
