import { test } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { SFSF_BASE, SFSF_COMPANY, SFSF_USERNAME, SFSF_PASSWORD, PY_BASE, PY_COMPANY, PY_USERNAME, PY_PASSWORD } from './config';

test.use({
  baseURL: PY_BASE,
  actionTimeout: 60000,
  navigationTimeout: 90000,
});

const FIXTURES = path.resolve(__dirname, '../fixtures/payroll');

function ensureDir(d: string) { if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true }); }

async function odata(page: any, name: string, url: string) {
  const res = await page.evaluate(async (u: string) => {
    const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
    return { status: r.status, body: await r.text() };
  }, url);
  fs.writeFileSync(path.join(FIXTURES, `${name}.json`), res.body);
  console.log(`  ${name}: HTTP ${res.status}, ${res.body.length} bytes`);
  return res;
}

test('Login to payroll instance and extract compensation data', async ({ page }) => {
  ensureDir(FIXTURES);

  // ---- LOGIN ----
  console.log('Navigating to payroll login...');
  await page.goto(`${PY_BASE}/sf/home?bplte_company=${PY_COMPANY}`);
  await page.waitForLoadState('networkidle');
  await page.screenshot({ path: `${FIXTURES}/01-login.png` });

  // Fill credentials
  await page.locator('input[name="username"], input[id="j_username"], input[type="text"]').first().fill(PY_USERNAME);
  await page.locator('input[name="password"], input[id="j_password"], input[type="password"]').first().fill(PY_PASSWORD);
  await page.screenshot({ path: `${FIXTURES}/02-filled.png` });
  await page.locator('button[type="submit"], input[type="submit"], button:has-text("Log In"), button:has-text("Login")').first().click();

  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(4000);
  await page.screenshot({ path: `${FIXTURES}/03-after-login.png` });
  console.log('Logged in. URL:', page.url());

  // ---- DISCOVER AVAILABLE ENTITIES (first record only) ----
  const discoveryEntities = [
    'EmpPayCompRecurring',
    'EmpPayCompNonRecurring',
    'EmpCompensation',
    'EmpJob',
    'User',
    'PayScalePayComponent',
    'PaymentInformationV3',
    'CompensationEmployee',
    'GLAccount',
    'PayCalendar',
  ];

  console.log('\nDiscovering available payroll entities...');
  for (const entity of discoveryEntities) {
    const url = `${BASE}/odata/v2/${entity}?$format=json&$top=1&company=${COMPANY_ID}`;
    const res = await page.evaluate(async (u: string) => {
      const r = await fetch(u, { credentials: 'include', headers: { Accept: 'application/json' } });
      return { status: r.status, body: await r.text() };
    }, url);

    let fields = '';
    if (res.status === 200) {
      try {
        const json = JSON.parse(res.body);
        const first = json.d?.results?.[0];
        if (first) fields = Object.keys(first).filter(k => k !== '__metadata').join(', ');
      } catch {}
    }
    console.log(`  ${entity}: HTTP ${res.status}${fields ? ' → ' + fields.slice(0, 120) : ' → ' + res.body.slice(0, 150).replace(/\n/g, ' ')}`);
  }

  // ---- FULL DATA PULLS for available entities ----
  console.log('\nExtracting full payroll datasets...');

  // User with salary field included
  await odata(page, 'py-employees',
    `${BASE}/odata/v2/User?$format=json&$top=500&$select=userId,firstName,lastName,department,division,jobTitle,hireDate,status,location,salary,salaryBudgetFinalSalaryPercentage,dateOfCurrentPosition&company=${COMPANY_ID}`);

  await odata(page, 'py-emp-job',
    `${BASE}/odata/v2/EmpJob?$format=json&$top=500&$select=userId,startDate,endDate,jobTitle,position,managerId,emplStatus,seqNumber&company=${COMPANY_ID}`);

  // Pull up to 2000 pay records to get full salary picture
  await odata(page, 'py-pay-recurring',
    `${BASE}/odata/v2/EmpPayCompRecurring?$format=json&$top=2000&company=${COMPANY_ID}`);

  // Non-recurring (bonuses, one-offs)
  await odata(page, 'py-pay-nonrecurring',
    `${BASE}/odata/v2/EmpPayCompNonRecurring?$format=json&$top=500&company=${COMPANY_ID}`);

  // Payment bank/method info
  await odata(page, 'py-payment-info',
    `${BASE}/odata/v2/PaymentInformationV3?$format=json&$top=500&company=${COMPANY_ID}`);

  // Employment record
  await odata(page, 'py-employment',
    `${BASE}/odata/v2/EmpEmployment?$format=json&$top=500&$select=userId,personIdExternal,startDate,endDate,firstDateWorked,lastDateWorked,originalStartDate,okToRehire,assignmentClass&company=${COMPANY_ID}`);

  // Screenshot final state
  await page.screenshot({ path: `${FIXTURES}/04-done.png` });

  console.log(`\nAll payroll files saved to: ${FIXTURES}`);
  console.log(fs.readdirSync(FIXTURES).filter(f => f.endsWith('.json')).join('\n'));
});
