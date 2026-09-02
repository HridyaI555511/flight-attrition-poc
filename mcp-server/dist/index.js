import { config as dotenvConfig } from 'dotenv';
import { chromium } from 'playwright';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import { execSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';
const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenvConfig({ path: path.resolve(__dirname, '..', '..', '.env') });
const ROOT = path.resolve(__dirname, '..', '..');
const FIXTURES_SFSF = path.join(ROOT, 'fixtures', 'sfsf');
const OUTPUT = path.join(ROOT, 'fixtures', 'output');
const SFSF_BASE = process.env.SFSF_BASE_URL ?? '';
const SFSF_COMPANY = process.env.SFSF_COMPANY ?? '';
const SFSF_USERNAME = process.env.SFSF_USERNAME ?? '';
const SFSF_PASSWORD = process.env.SFSF_PASSWORD ?? '';
if (!SFSF_BASE || !SFSF_COMPANY || !SFSF_USERNAME || !SFSF_PASSWORD) {
    console.error('Missing required env vars: SFSF_BASE_URL, SFSF_COMPANY, SFSF_USERNAME, SFSF_PASSWORD');
    process.exit(1);
}
// ── Session state ────────────────────────────────────────────────────────────
let browser = null;
let context = null;
let sessionCookieHeader = '';
let lastAuthAt = 0;
const SESSION_TTL_MS = 30 * 60 * 1000; // 30 minutes
async function ensureAuth() {
    const now = Date.now();
    if (sessionCookieHeader && now - lastAuthAt < SESSION_TTL_MS) {
        return sessionCookieHeader;
    }
    console.error('[mcp-sf] Authenticating via Playwright...');
    if (!browser) {
        browser = await chromium.launch({ headless: true });
    }
    if (context) {
        await context.close().catch(() => { });
    }
    context = await browser.newContext();
    const page = await context.newPage();
    await page.goto(`${SFSF_BASE}/login?company=${SFSF_COMPANY}`, { waitUntil: 'networkidle' });
    await page.locator('input[type="text"]').first().fill(SFSF_USERNAME);
    await page.locator('input[type="password"]').first().fill(SFSF_PASSWORD);
    await page.locator('button[type="submit"], input[type="submit"]').first().click();
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);
    const cookies = await context.cookies();
    sessionCookieHeader = cookies.map(c => `${c.name}=${c.value}`).join('; ');
    lastAuthAt = Date.now();
    await page.close();
    console.error(`[mcp-sf] Authenticated. Cookies: ${cookies.length} stored.`);
    return sessionCookieHeader;
}
async function odataFetch(entityPath, params = {}) {
    const cookieHeader = await ensureAuth();
    const qs = new URLSearchParams({ $format: 'json', ...params }).toString();
    const url = `${SFSF_BASE}/odata/v2/${entityPath}?${qs}`;
    const res = await fetch(url, {
        headers: {
            Accept: 'application/json',
            Cookie: cookieHeader,
        },
    });
    const raw = await res.text();
    // If we get a 401, session expired — clear and retry once
    if (res.status === 401) {
        sessionCookieHeader = '';
        const cookieHeader2 = await ensureAuth();
        const res2 = await fetch(url, {
            headers: { Accept: 'application/json', Cookie: cookieHeader2 },
        });
        const raw2 = await res2.text();
        let data2 = raw2;
        try {
            data2 = JSON.parse(raw2);
        }
        catch { }
        return { status: res2.status, data: data2, raw: raw2 };
    }
    let data = raw;
    try {
        data = JSON.parse(raw);
    }
    catch { }
    return { status: res.status, data, raw };
}
// ── MCP Server ───────────────────────────────────────────────────────────────
const server = new McpServer({
    name: 'sf-attrition',
    version: '1.0.0',
});
// Tool: authenticate (explicit login / session refresh)
server.tool('authenticate', 'Log in to SAP SuccessFactors via Playwright and cache the session cookie.', {}, async () => {
    sessionCookieHeader = '';
    await ensureAuth();
    return { content: [{ type: 'text', text: 'Authenticated successfully. Session cached for 30 minutes.' }] };
});
// Tool: fetch_entity — generic OData entity fetch
server.tool('fetch_entity', 'Fetch records from any SAP SuccessFactors OData v2 entity.', {
    entity: z.string().describe('OData entity name, e.g. "Employee", "EmpCompensation"'),
    top: z.number().optional().default(1000).describe('Max records to return ($top)'),
    select: z.string().optional().describe('Comma-separated field names ($select)'),
    filter: z.string().optional().describe('OData filter expression ($filter)'),
    expand: z.string().optional().describe('Navigation property to expand ($expand)'),
    save_as: z.string().optional().describe('If provided, save raw JSON to fixtures/sfsf/<save_as>.json'),
}, async ({ entity, top, select, filter, expand, save_as }) => {
    const params = { $top: String(top ?? 1000) };
    if (select)
        params['$select'] = select;
    if (filter)
        params['$filter'] = filter;
    if (expand)
        params['$expand'] = expand;
    const { status, data, raw } = await odataFetch(entity, params);
    if (save_as) {
        fs.mkdirSync(FIXTURES_SFSF, { recursive: true });
        fs.writeFileSync(path.join(FIXTURES_SFSF, `${save_as}.json`), raw);
    }
    const results = data?.d?.results ?? [];
    const count = Array.isArray(results) ? results.length : 0;
    const preview = Array.isArray(results) && results.length > 0
        ? Object.keys(results[0]).filter(k => k !== '__metadata').slice(0, 8).join(', ')
        : '';
    return {
        content: [{
                type: 'text',
                text: [
                    `HTTP ${status} | ${count} records | Fields: ${preview}`,
                    save_as ? `Saved to fixtures/sfsf/${save_as}.json` : '',
                    count > 0 ? `\nSample (first record):\n${JSON.stringify(results[0], null, 2).substring(0, 800)}` : '',
                ].filter(Boolean).join('\n'),
            }],
    };
});
// Tool: fetch_and_save_all — fetch all standard entities used by the model
server.tool('fetch_and_save_all', 'Fetch all SF entities needed for the attrition model and save them to fixtures/sfsf/. This replaces the Playwright test suite.', {
    entities: z.array(z.object({
        entity: z.string(),
        name: z.string().describe('Output file name (without .json)'),
        top: z.number().optional(),
        select: z.string().optional(),
        filter: z.string().optional(),
    })).optional().describe('Override the default entity list'),
}, async ({ entities }) => {
    const DEFAULT_ENTITIES = [
        { entity: 'Employee', name: 'employees', top: 2000 },
        { entity: 'Employment', name: 'employment', top: 2000 },
        { entity: 'EmpJob', name: 'emp-job', top: 2000 },
        { entity: 'PerformanceForm', name: 'performance-forms', top: 2000 },
        { entity: 'Compensation', name: 'compensation', top: 2000 },
        { entity: 'EmployeeTime', name: 'employee-time', top: 2000 },
        { entity: 'TimeAccount', name: 'time-account', top: 2000 },
        { entity: 'TimeAccountDetail', name: 'time-account-detail', top: 2000 },
        { entity: 'JobApplication', name: 'job-applications', top: 2000 },
        { entity: 'EmpCompensation', name: 'emp-compensation', top: 1000 },
        { entity: 'EmpPayCompRecurring', name: 'emp-pay-recurring', top: 2000 },
        { entity: 'PerPerson', name: 'per-person', top: 1000 },
        { entity: 'PerPersonal', name: 'per-personal', top: 1000 },
        { entity: 'Position', name: 'position', top: 1000 },
        { entity: 'CalibrationSession', name: 'calibration-sessions', top: 500 },
        { entity: 'JobRequisition', name: 'job-requisitions', top: 500 },
    ];
    const list = entities ?? DEFAULT_ENTITIES;
    fs.mkdirSync(FIXTURES_SFSF, { recursive: true });
    const results = [];
    for (const e of list) {
        const params = { $top: String(e.top ?? 1000) };
        if (e.select)
            params['$select'] = e.select;
        if (e.filter)
            params['$filter'] = e.filter;
        const { status, raw } = await odataFetch(e.entity, params);
        fs.writeFileSync(path.join(FIXTURES_SFSF, `${e.name}.json`), raw);
        let count = 0;
        try {
            count = JSON.parse(raw).d?.results?.length ?? 0;
        }
        catch { }
        results.push(`  ${e.entity} → ${e.name}.json: HTTP ${status}, ${count} records`);
    }
    return {
        content: [{
                type: 'text',
                text: `Fetched ${list.length} entities:\n${results.join('\n')}`,
            }],
    };
});
// Tool: run_model — execute the Python attrition model
server.tool('run_model', 'Run the Python attrition risk model and rebuild the HTML dashboard.', {
    dashboard_only: z.boolean().optional().default(false).describe('If true, only rebuild the dashboard (skip model re-scoring)'),
}, async ({ dashboard_only }) => {
    const cmd = dashboard_only
        ? `cd "${ROOT}" && python3 model/build_dashboard.py`
        : `cd "${ROOT}" && python3 model/attrition_enriched.py && python3 model/build_dashboard.py`;
    try {
        const out = execSync(cmd, { encoding: 'utf8', timeout: 120000 });
        return { content: [{ type: 'text', text: out.trim() }] };
    }
    catch (e) {
        return { content: [{ type: 'text', text: `Error: ${e.message}\n${e.stdout ?? ''}` }] };
    }
});
// Tool: get_risk_summary — read the latest model output
server.tool('get_risk_summary', 'Read the latest attrition model summary JSON (risk bands, factor weights, coverage stats).', {}, async () => {
    const p = path.join(OUTPUT, 'attrition_enriched_summary.json');
    if (!fs.existsSync(p)) {
        return { content: [{ type: 'text', text: 'No summary found. Run run_model first.' }] };
    }
    const data = fs.readFileSync(p, 'utf8');
    return { content: [{ type: 'text', text: data }] };
});
// Tool: get_high_risk_employees — read top high-risk employee list
server.tool('get_high_risk_employees', 'Return the top N high-risk employees from the last model run.', {
    top_n: z.number().optional().default(20).describe('Number of employees to return'),
}, async ({ top_n }) => {
    const p = path.join(OUTPUT, 'high_risk_enriched_explanations.csv');
    if (!fs.existsSync(p)) {
        return { content: [{ type: 'text', text: 'No high-risk data found. Run run_model first.' }] };
    }
    const lines = fs.readFileSync(p, 'utf8').split('\n').slice(0, (top_n ?? 20) + 1);
    return { content: [{ type: 'text', text: lines.join('\n') }] };
});
// ── Start ────────────────────────────────────────────────────────────────────
const transport = new StdioServerTransport();
await server.connect(transport);
console.error('[mcp-sf] SAP SuccessFactors MCP server running on stdio');
