import { test } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { SFSF_BASE, SFSF_COMPANY, SFSF_USERNAME, SFSF_PASSWORD, PY_BASE, PY_COMPANY, PY_USERNAME, PY_PASSWORD } from './config';

test.use({
  baseURL: SFSF_BASE,
  actionTimeout: 60000,
  navigationTimeout: 90000,
});

const FIXTURES = path.resolve(__dirname, '../fixtures/sfsf');

function ensureDir(dir: string) {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
}

test('Login to SuccessFactors and download all relevant reports', async ({ page }) => {
  ensureDir(FIXTURES);

  // ---- LOGIN ----
  console.log('Navigating to login page...');
  await page.goto(`${SFSF_BASE}/login?company=${SFSF_COMPANY}`);
  await page.waitForLoadState('networkidle');
  await page.screenshot({ path: `${FIXTURES}/01-login-page.png` });

  await page.locator('input[name="username"], input[id="j_username"], input[type="text"]').first().fill(SFSF_USERNAME);
  await page.locator('input[name="password"], input[id="j_password"], input[type="password"]').first().fill(SFSF_PASSWORD);
  await page.screenshot({ path: `${FIXTURES}/02-credentials-filled.png` });

  await page.locator('button[type="submit"], input[type="submit"], button:has-text("Log In"), button:has-text("Login"), button:has-text("Sign In")').first().click();
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(4000);
  await page.screenshot({ path: `${FIXTURES}/03-after-login.png` });
  console.log('Logged in. Current URL:', page.url());

  // ---- NAVIGATE TO REPORTS ----
  // Try clicking the main nav / hamburger menu to find Reports
  console.log('Looking for Reports/Analytics in navigation...');

  // Try direct navigation to common SFSF report URLs
  const reportUrls = [
    '/sf/reports?company=${SFSF_COMPANY}',
    '/analytics/main?company=${SFSF_COMPANY}',
    '/sf/analyticsCenter?company=${SFSF_COMPANY}',
    '/sf/reporting?company=${SFSF_COMPANY}',
  ];

  for (const url of reportUrls) {
    console.log(`Trying: ${url}`);
    await page.goto(`https://salesdemo.successfactors.eu${url}`);
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);
    const slug = url.replace(/[^a-z]/gi, '-');
    await page.screenshot({ path: `${FIXTURES}/nav${slug}.png` });
    console.log('  URL after navigation:', page.url());
  }

  // ---- FIND REPORTS VIA HOME NAV ----
  await page.goto('https://salesdemo.successfactors.eu/sf/start?company=${SFSF_COMPANY}');
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${FIXTURES}/04-home.png` });

  // Click hamburger / nav menu
  const navMenu = page.locator('button[aria-label*="menu"], button[aria-label*="Menu"], .nav-hamburger, #hamburger, [class*="hamburger"]').first();
  if (await navMenu.isVisible()) {
    await navMenu.click();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `${FIXTURES}/05-nav-open.png` });
  }

  // Look for "Reports" or "Analytics" link
  const reportsLink = page.locator('a:has-text("Reports"), a:has-text("Analytics"), a:has-text("Reporting"), span:has-text("Reports"), span:has-text("Analytics")').first();
  if (await reportsLink.isVisible()) {
    console.log('Found Reports link, clicking...');
    await reportsLink.click();
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(3000);
    await page.screenshot({ path: `${FIXTURES}/06-reports-page.png` });
    console.log('Reports page URL:', page.url());
  }

  // ---- LOOK FOR WORKFORCE DATA / EMPLOYEE DATA TILES ----
  await page.screenshot({ path: `${FIXTURES}/07-reports-overview.png` });

  // Capture all visible links/tiles on the page that look like reports
  const links = await page.evaluate(() =>
    Array.from(document.querySelectorAll('a[href]')).map(a => ({
      text: (a as HTMLElement).innerText?.trim(),
      href: (a as HTMLAnchorElement).href,
    })).filter(l => l.text && l.text.length > 2)
  );
  fs.writeFileSync(`${FIXTURES}/all-page-links.json`, JSON.stringify(links, null, 2));
  console.log(`Found ${links.length} links on page`);

  // ---- DOWNLOAD VIA ODATA (cookie session) ----
  // Grab cookies from the authenticated session
  const odataQueries = [
    {
      name: 'employees',
      url: 'https://salesdemo.successfactors.eu/odata/v2/User?$format=json&$top=500&$select=userId,firstName,lastName,department,division,jobTitle,hireDate,status,location',
    },
    {
      name: 'emp-job',
      url: 'https://salesdemo.successfactors.eu/odata/v2/EmpJob?$format=json&$top=500&$select=userId,startDate,endDate,jobTitle,position,managerId,emplStatus,seqNumber',
    },
    {
      name: 'compensation',
      url: 'https://salesdemo.successfactors.eu/odata/v2/EmpCompensation?$format=json&$top=500&$select=userId,startDate,endDate,event,eventReason,bonusTarget,payGroup',
    },
    {
      name: 'performance-forms',
      url: 'https://salesdemo.successfactors.eu/odata/v2/FormHeader?$format=json&$top=500&$select=formDataId,rating,formSubjectId,formTitle,formTemplateType,isRated,formDataStatus,formReviewStartDate,formReviewEndDate,dateAssigned',
    },
    {
      name: 'employment',
      url: 'https://salesdemo.successfactors.eu/odata/v2/EmpEmployment?$format=json&$top=500&$select=userId,personIdExternal,startDate,endDate,firstDateWorked,lastDateWorked,originalStartDate,okToRehire,assignmentClass',
    },
    {
      name: 'pay-compensation',
      url: 'https://salesdemo.successfactors.eu/odata/v2/EmpPayCompRecurring?$format=json&$top=500',
    },
  ];

  for (const query of odataQueries) {
    console.log(`Fetching OData: ${query.name}`);
    const response = await page.evaluate(async ({ url }) => {
      const res = await fetch(url, {
        credentials: 'include',
        headers: { Accept: 'application/json' },
      });
      return { status: res.status, body: await res.text() };
    }, { url: query.url });

    console.log(`  ${query.name}: HTTP ${response.status}, ${response.body.length} bytes`);
    fs.writeFileSync(`${FIXTURES}/${query.name}.json`, response.body);
  }

  // ---- NAVIGATE TO EACH RELEVANT REPORT AND SCREENSHOT ----
  const attritionKeywords = ['attrition', 'turnover', 'retention', 'headcount', 'workforce', 'compensation', 'performance', 'engagement'];
  const relevantLinks = links.filter(l =>
    attritionKeywords.some(kw => l.text.toLowerCase().includes(kw) || l.href.toLowerCase().includes(kw))
  );

  console.log(`Found ${relevantLinks.length} attrition-relevant links`);
  fs.writeFileSync(`${FIXTURES}/relevant-links.json`, JSON.stringify(relevantLinks, null, 2));

  for (let i = 0; i < Math.min(relevantLinks.length, 10); i++) {
    const link = relevantLinks[i];
    console.log(`Opening report: ${link.text} → ${link.href}`);
    await page.goto(link.href);
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(3000);
    await page.screenshot({ path: `${FIXTURES}/report-${i + 1}-${link.text.replace(/\W+/g, '-').slice(0, 40)}.png` });
  }

  console.log(`\nAll files saved to: ${FIXTURES}`);
  console.log(fs.readdirSync(FIXTURES).join('\n'));
});
