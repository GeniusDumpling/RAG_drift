import '@testing-library/jest-dom/vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { CrawlJob, CrawlRun, Page, SourceSite } from '../src/api/types';
import { SourcesPage } from '../src/pages/SourcesPage';

const EMPTY_STATE_COPY = 'No data loaded yet. Start the backend and run the demo seed script.';

const source: SourceSite = {
  id: 'source-1',
  name: 'Official Docs',
  site_type: 'docs',
  base_url: 'https://example.test/docs',
  allowed_domains: ['example.test'],
  fetch_mode: 'http',
  default_language: 'en',
  active: true,
  config_json: {},
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const job: CrawlJob = {
  id: 'job-1',
  source_site_id: 'source-1',
  name: 'Daily Crawl',
  trigger_mode: 'manual',
  cron_expr: null,
  seed_config_json: {},
  parser_profile: 'docs',
  max_pages: 5,
  enabled: true,
  agent_policy_json: {},
  last_run_at: null,
  next_run_at: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const run: CrawlRun = {
  id: 'run-1',
  source_site_id: 'source-1',
  crawl_job_id: 'job-1',
  trigger_type: 'manual',
  execution_mode: 'foreground',
  seed_url: null,
  status: 'queued',
  started_at: null,
  finished_at: null,
  discovered_count: 0,
  fetched_count: 0,
  parsed_count: 0,
  extracted_count: 0,
  deduped_count: 0,
  chunked_count: 0,
  embedded_count: 0,
  error_count: 0,
  config_snapshot_json: {},
  error_message: null,
  created_at: '2026-01-01T00:00:00Z',
};

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, limit: 50, offset: 0 };
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('SourcesPage', () => {
  it('renders sources and jobs and triggers an enabled crawl job', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/sources?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse(page<SourceSite>([source])));
      }
      if (url.endsWith('/jobs?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse(page<CrawlJob>([job])));
      }
      if (url.endsWith('/jobs/job-1/trigger') && init?.method === 'POST') {
        return Promise.resolve(jsonResponse(run));
      }
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<SourcesPage />);

    expect(await screen.findByRole('heading', { name: 'Official Docs' })).toBeInTheDocument();
    expect(screen.getByText('https://example.test/docs')).toBeInTheDocument();
    expect(screen.getByText('Daily Crawl')).toBeInTheDocument();
    expect(
      screen.getByText((_content, element) => element?.textContent === 'Daily Crawl · docs · max 5'),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Trigger Daily Crawl' }));

    expect(await screen.findByText('Queued run run-1 for Daily Crawl.')).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const triggerCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith('/jobs/job-1/trigger'));
    expect(triggerCall).toBeDefined();
    const [, init] = triggerCall!;
    expect(init).toMatchObject({ method: 'POST' });
    expect(JSON.parse(String(init?.body))).toEqual({ seed_url: null });
  });

  it('disables a job trigger while pending and prevents duplicate POSTs', async () => {
    let resolveTrigger!: (response: Response) => void;
    const pendingTrigger = new Promise<Response>((resolve) => {
      resolveTrigger = resolve;
    });
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/sources?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse(page<SourceSite>([source])));
      }
      if (url.endsWith('/jobs?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse(page<CrawlJob>([job])));
      }
      if (url.endsWith('/jobs/job-1/trigger') && init?.method === 'POST') {
        return pendingTrigger;
      }
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<SourcesPage />);

    const triggerButton = await screen.findByRole('button', { name: 'Trigger Daily Crawl' });
    fireEvent.click(triggerButton);

    expect(triggerButton).toBeDisabled();
    fireEvent.click(triggerButton);
    expect(fetchMock.mock.calls.filter(([input]) => String(input).endsWith('/jobs/job-1/trigger'))).toHaveLength(1);

    await act(async () => {
      resolveTrigger(jsonResponse(run));
    });

    expect(await screen.findByText('Queued run run-1 for Daily Crawl.')).toBeInTheDocument();
    await waitFor(() => expect(triggerButton).not.toBeDisabled());
  });

  it('renders an explicit API error when sources or jobs fail to load', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('backend offline')));

    render(<SourcesPage />);

    expect(await screen.findByRole('alert')).toHaveTextContent('Unable to load sources data: backend offline');
    expect(screen.getByText('Source data unavailable while the API request is failing.')).toBeInTheDocument();
    expect(screen.queryByText(EMPTY_STATE_COPY)).not.toBeInTheDocument();
  });
});
