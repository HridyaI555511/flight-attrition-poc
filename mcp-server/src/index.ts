import { config as dotenvConfig } from 'dotenv';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import { execSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import { fileURLToPath } from 'url';
import { chromium, type Browser, type Page } from 'playwright';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenvConfig({ path: path.resolve(__dirname, '..', '..', '.env') });

const ROOT           = path.resolve(__dirname, '..', '..');
const FIXTURES_SFSF  = path.join(ROOT, 'fixtures', 'sfsf');
const OUTPUT         = path.join(ROOT, 'fixtures', 'output');

const SFSF_BASE     = process.env.SFSF_BASE_URL ?? '';
const SFSF_COMPANY  = process.env.SFSF_COMPANY  ?? '';
const SFSF_USERNAME = process.env.SFSF_USERNAME  ?? '';
const SFSF_PASSWORD = process.env.SFSF_PASSWORD  ?? '';

if (!SFSF_BASE || !SFSF_COMPANY || !SFSF_USERNAME || !SFSF_PASSWORD) {
  console.error('Missing required env vars: SFSF_BASE_URL, SFSF_COMPANY, SFSF_USERNAME, SFSF_PASSWORD');
  process.exit(1);
}

// ── SF Browser Session (SSO-based — Basic Auth is blocked on salesdemo tenant) ─
// salesdemo.successfactors.eu routes API calls through SAP IAS SSO.
// GET /odata/v2/* works with a live browser session without a CSRF token.
// We keep one headless Chromium session alive for the MCP server lifetime.

class SFBrowserSession {
  private browser: Browser | null = null;
  private page: Page | null = null;
  private loggedIn = false;

  private async ensureBrowser(): Promise<void> {
    if (!this.browser) {
      this.browser = await chromium.launch({ headless: true });
    }
  }

  async ensurePage(): Promise<Page> {
    await this.ensureBrowser();
    if (!this.page) {
      const ctx = await this.browser!.newContext();
      this.page = await ctx.newPage();
    }
    if (!this.loggedIn) {
      await this.login();
    }
    return this.page;
  }

  private async login(): Promise<void> {
    const p = this.page!;
    console.error('[mcp-sf] Logging in via SSO...');
    await p.goto(`${SFSF_BASE}/login?company=${SFSF_COMPANY}`, { timeout: 40000 });
    // SAP IAS SSO page — fill Email/User Name, click Continue, fill Password, click Continue
    await p.fill('input[placeholder="Email or User Name"], input[type="text"]', SFSF_USERNAME).catch(() => {});
    await p.click('button:has-text("Continue")').catch(() => {});
    await p.waitForTimeout(2000);
    await p.fill('input[type="password"]', SFSF_PASSWORD).catch(() => {});
    await p.click('button:has-text("Continue")').catch(() => {});
    await p.waitForTimeout(7000);
    this.loggedIn = p.url().includes('/sf/') || p.url().includes('successfactors');
    console.error(`[mcp-sf] Login ${this.loggedIn ? 'succeeded' : 'failed'}: ${p.url()}`);
  }

  async fetch(entityPath: string, params: Record<string, string>): Promise<{ status: number; data: unknown; raw: string }> {
    const p = await this.ensurePage();
    const qs = new URLSearchParams({ $format: 'json', ...params }).toString();
    const url = `/odata/v2/${entityPath}?${qs}`;

    const result = await p.evaluate(async ([fetchUrl]: [string]) => {
      const r = await fetch(fetchUrl, {
        credentials: 'include',
        headers: { Accept: 'application/json' },
      });
      return { status: r.status, body: await r.text() };
    }, [url] as [string]);

    // On 401 re-login once and retry
    if (result.status === 401) {
      this.loggedIn = false;
      await this.login();
      const retry = await p.evaluate(async ([fetchUrl]: [string]) => {
        const r = await fetch(fetchUrl, {
          credentials: 'include',
          headers: { Accept: 'application/json' },
        });
        return { status: r.status, body: await r.text() };
      }, [url] as [string]);
      let data: unknown = retry.body;
      try { data = JSON.parse(retry.body); } catch {}
      return { status: retry.status, data, raw: retry.body };
    }

    let data: unknown = result.body;
    try { data = JSON.parse(result.body); } catch {}
    return { status: result.status, data, raw: result.body };
  }

  async close(): Promise<void> {
    await this.browser?.close();
    this.browser = null;
    this.page = null;
    this.loggedIn = false;
  }
}

const sfSession = new SFBrowserSession();

// ── MCP Server ────────────────────────────────────────────────────────────────
const server = new McpServer({ name: 'sf-attrition', version: '2.0.0' });

// Tool: authenticate
server.tool(
  'authenticate',
  'Log in to SAP SuccessFactors via Playwright and cache the session cookie.',
  {},
  async () => {
    try {
      const { status } = await sfSession.fetch('User', { $top: '1', $select: 'userId' });
      if (status === 200) return { content: [{ type: 'text', text: 'Authentication successful (Browser SSO).' }] };
      return { content: [{ type: 'text', text: `Authentication failed. HTTP ${status} — check credentials.` }] };
    } catch (e: any) {
      return { content: [{ type: 'text', text: `Authentication error: ${e.message}` }] };
    }
  }
);

// Tool: fetch_entity
server.tool(
  'fetch_entity',
  'Fetch records from any SAP SuccessFactors OData v2 entity.',
  {
    entity:  z.string().describe('OData entity name, e.g. "User", "EmpJob"'),
    top:     z.number().optional().default(1000),
    select:  z.string().optional(),
    filter:  z.string().optional(),
    expand:  z.string().optional(),
    save_as: z.string().optional().describe('Save raw JSON to fixtures/sfsf/<save_as>.json'),
  },
  async ({ entity, top, select, filter, expand, save_as }) => {
    const params: Record<string, string> = { $top: String(top ?? 1000) };
    if (select) params['$select'] = select;
    if (filter) params['$filter'] = filter;
    if (expand) params['$expand'] = expand;

    const { status, data, raw } = await sfSession.fetch(entity, params);

    if (save_as) {
      fs.mkdirSync(FIXTURES_SFSF, { recursive: true });
      fs.writeFileSync(path.join(FIXTURES_SFSF, `${save_as}.json`), raw);
    }

    const results = (data as any)?.d?.results ?? [];
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
          count > 0 ? `\nSample:\n${JSON.stringify(results[0], null, 2).substring(0, 600)}` : '',
        ].filter(Boolean).join('\n'),
      }],
    };
  }
);

// Tool: fetch_and_save_all
server.tool(
  'fetch_and_save_all',
  'Fetch all SF entities needed for the attrition model and save them to fixtures/sfsf/. This replaces the Playwright test suite.',
  {
    entities: z.array(z.object({
      entity:  z.string(),
      name:    z.string().describe('Output file name (without .json)'),
      top:     z.number().optional(),
      select:  z.string().optional(),
      filter:  z.string().optional(),
    })).optional(),
  },
  async ({ entities }) => {
    // Entity names confirmed working via browser session on salesdemo tenant
    const SF_ENTITIES: Array<{ entity: string; name: string; top?: number; select?: string }> = [
      { entity: 'User',                name: 'employees',            top: 2000 },
      { entity: 'EmpEmployment',       name: 'employment',           top: 2000 },
      { entity: 'EmpJob',              name: 'emp-job',              top: 2000 },
      { entity: 'FormHeader',          name: 'performance-forms',    top: 2000 },
      { entity: 'EmployeeTime',        name: 'employee-time',        top: 2000 },
      { entity: 'TimeAccount',         name: 'time-account',         top: 2000 },
      { entity: 'TimeAccountDetail',   name: 'time-account-detail',  top: 2000 },
      { entity: 'JobApplication',      name: 'job-applications',     top: 2000 },
      { entity: 'EmpCompensation',     name: 'emp-compensation',     top: 1000 },
      { entity: 'EmpPayCompRecurring', name: 'emp-pay-recurring',    top: 2000 },
      { entity: 'PerPerson',           name: 'per-person',           top: 1000 },
      { entity: 'PerPersonal',         name: 'per-personal',         top: 1000 },
      { entity: 'CalibrationSession',  name: 'calibration-sessions', top: 500  },
      { entity: 'JobRequisition',      name: 'job-requisitions',     top: 500  },
    ];

    const list = entities ?? SF_ENTITIES;
    fs.mkdirSync(FIXTURES_SFSF, { recursive: true });

    const results: string[] = [];
    for (const e of list) {
      const params: Record<string, string> = { $top: String(e.top ?? 1000) };
      if (e.select) params['$select'] = e.select;
      if ((e as any).filter) params['$filter'] = (e as any).filter;
      const { status, raw } = await sfSession.fetch(e.entity, params);
      // Only save if we got valid JSON (guard against error-page overwrites)
      let count = 0;
      let valid = false;
      try {
        const parsed = JSON.parse(raw);
        count = parsed?.d?.results?.length ?? 0;
        valid = true;
      } catch {}
      if (valid) {
        fs.writeFileSync(path.join(FIXTURES_SFSF, `${e.name}.json`), raw);
        results.push(`  ${e.entity} → ${e.name}.json: HTTP ${status}, ${count} records`);
      } else {
        results.push(`  ${e.entity} → SKIPPED (invalid JSON, HTTP ${status})`);
      }
    }

    return {
      content: [{
        type: 'text',
        text: `Fetched ${list.length} entities:\n${results.join('\n')}`,
      }],
    };
  }
);

// Tool: run_model
server.tool(
  'run_model',
  'Run the Python attrition risk model and rebuild the HTML dashboard.',
  { dashboard_only: z.boolean().optional().default(false) },
  async ({ dashboard_only }) => {
    const cmd = dashboard_only
      ? `cd "${ROOT}" && python3 model/build_dashboard.py`
      : `cd "${ROOT}" && python3 model/attrition_enriched.py && python3 model/build_dashboard.py`;
    try {
      const out = execSync(cmd, { encoding: 'utf8', timeout: 180000 });
      return { content: [{ type: 'text', text: out.trim() }] };
    } catch (e: any) {
      return { content: [{ type: 'text', text: `Error: ${e.message}\n${e.stdout ?? ''}` }] };
    }
  }
);

// Tool: get_risk_summary
server.tool(
  'get_risk_summary',
  'Read the latest attrition model summary JSON (risk bands, factor weights, coverage stats).',
  {},
  async () => {
    const p = path.join(OUTPUT, 'attrition_enriched_summary.json');
    if (!fs.existsSync(p)) return { content: [{ type: 'text', text: 'No summary found. Run run_model first.' }] };
    return { content: [{ type: 'text', text: fs.readFileSync(p, 'utf8') }] };
  }
);

// Tool: get_high_risk_employees
server.tool(
  'get_high_risk_employees',
  'Return the top N high-risk employees from the last model run.',
  { top_n: z.number().optional().default(20) },
  async ({ top_n }) => {
    const p = path.join(OUTPUT, 'high_risk_enriched_explanations.csv');
    if (!fs.existsSync(p)) return { content: [{ type: 'text', text: 'No high-risk data found. Run run_model first.' }] };
    const lines = fs.readFileSync(p, 'utf8').split('\n').slice(0, (top_n ?? 20) + 1);
    return { content: [{ type: 'text', text: lines.join('\n') }] };
  }
);

// ── Start ─────────────────────────────────────────────────────────────────────
const transport = new StdioServerTransport();
await server.connect(transport);
console.error('[mcp-sf] SAP SuccessFactors MCP server running (Browser SSO mode)');

process.on('SIGTERM', async () => { await sfSession.close(); process.exit(0); });
process.on('SIGINT',  async () => { await sfSession.close(); process.exit(0); });
