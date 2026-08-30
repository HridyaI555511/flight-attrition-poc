import { test } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { SFSF_BASE, SFSF_COMPANY, SFSF_USERNAME, SFSF_PASSWORD, PY_BASE, PY_COMPANY, PY_USERNAME, PY_PASSWORD } from './config';

const SFSF_FIX  = path.resolve(__dirname, '../fixtures/sfsf');
const PY_FIX    = path.resolve(__dirname, '../fixtures/payroll');

test.use({ actionTimeout: 90000, navigationTimeout: 90000 });

const SEL = `userId,startDate,endDate,seqNumber,jobTitle,managerId,department,division,location,eventReason,occupationalLevels`;

async function fetchAllPages(page: any, base: string, entity: string, params: string): Promise<any[]> {
  const all: any[] = [];
  let skip = 0;
  const top = 1000;
  while (true) {
    const url = `${base}/odata/v2/${entity}?$format=json&$top=${top}&$skip=${skip}&${params}&$select=${SEL}`;
    const res = await page.evaluate(async (u: string) => {
      const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
      return { status: r.status, body: await r.text() };
    }, url);
    if (res.status !== 200) { console.log(`  Error ${res.status} at skip=${skip}`); break; }
    const recs = JSON.parse(res.body).d?.results || [];
    all.push(...recs);
    console.log(`  skip=${skip}: got ${recs.length} records (total so far: ${all.length})`);
    if (recs.length < top) break;
    skip += top;
  }
  return all;
}

test('Fetch full EmpJob history with pagination', async ({ page }) => {
  // ── SFSF ──────────────────────────────────────────────────────────────────
  await page.goto(`${SFSF_BASE}/login?company=${SFSF_COMPANY}`);
  await page.waitForLoadState('networkidle');
  await page.locator('input[type="text"]').first().fill('sfadmin');
  await page.locator('input[type="password"]').first().fill('Demo2026!');
  await page.locator('button[type="submit"], input[type="submit"]').first().click();
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(3000);
  console.log('\n══ SFSF — paginated EmpJob history ══');

  const sfsfRecs = await fetchAllPages(page, SFSF_BASE, 'EmpJob',
    'fromDate=1990-01-01&toDate=2099-12-31');
  const sfsfUsers = new Set(sfsfRecs.map((r: any) => r.userId)).size;
  console.log(`SFSF total: ${sfsfRecs.length} records, ${sfsfUsers} unique users`);
  fs.writeFileSync(path.join(SFSF_FIX, 'emp-job-history.json'),
    JSON.stringify({ d: { results: sfsfRecs } }));

  // ── Payroll ───────────────────────────────────────────────────────────────
  await page.goto(`${PY_BASE}/sf/home?bplte_company=${PY_COMPANY}`);
  await page.waitForLoadState('networkidle');
  await page.locator('input[type="text"]').first().fill('103200');
  await page.locator('input[type="password"]').first().fill('PYdemo@2024');
  await page.locator('button[type="submit"], input[type="submit"]').first().click();
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(3000);
  console.log('\n══ Payroll — paginated EmpJob history ══');

  const pyRecs = await fetchAllPages(page, PY_BASE, 'EmpJob',
    'fromDate=1990-01-01&toDate=2099-12-31');
  const pyUsers = new Set(pyRecs.map((r: any) => r.userId)).size;
  console.log(`Payroll total: ${pyRecs.length} records, ${pyUsers} unique users`);
  fs.writeFileSync(path.join(PY_FIX, 'py-emp-job-history.json'),
    JSON.stringify({ d: { results: pyRecs } }));

  console.log('\n✅ Done.');
});
