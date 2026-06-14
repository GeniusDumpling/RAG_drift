import '@testing-library/jest-dom/vitest';
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { RunDetailPage } from '../src/pages/RunDetailPage';
import type { CrawlRun, CrawlRunEvent, Page } from '../src/api/types';

const EMPTY_STATE_COPY = 'No data loaded yet. Start the backend and run the demo seed script.';

const run: CrawlRun = {
  id: 'run-1',
  source_site_id: 'source-1',
  crawl_job_id: 'job-1',
  trigger_type: 'manual',
  execution_mode: 'foreground',
  seed_url: 'https://example.test/start',
  status: 'success',
  started_at: '2026-01-01T00:00:00Z',
  finished_at: '2026-01-01T00:05:00Z',
  discovered_count: 4,
  fetched_count: 3,
  parsed_count: 2,
  extracted_count: 2,
  deduped_count: 1,
  chunked_count: 6,
  embedded_count: 6,
  error_count: 0,
  config_snapshot_json: { max_pages: 4 },
  error_message: null,
  created_at: '2026-01-01T00:00:00Z',
};

const event: CrawlRunEvent = {
  id: 'event-1',
  crawl_run_id: 'run-1',
  stage: 'fetch',
  level: 'info',
  event_type: 'page_fetched',
  message: 'Fetched official setup guide.',
  related_url: 'https://example.test/start',
  related_raw_page_id: 'raw-1',
  related_content_item_id: 'content-1',
  counters_json: { fetched_count: 3 },
  agent_trace_json: { extraction: 'validated' },
  created_at: '2026-01-01T00:02:00Z',
};

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, limit: 25, offset: 0 };
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

describe('RunDetailPage', () => {
  it('renders run counters and event timeline headings', () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(page<CrawlRun>([]))));

    render(<RunDetailPage />);

    expect(screen.getByText('Run Detail')).toBeInTheDocument();
    expect(screen.getByText('Stage Counters')).toBeInTheDocument();
    expect(screen.getByText('Event Timeline')).toBeInTheDocument();
  });

  it('renders fetched run counters and events from the default loaded run', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/runs?limit=25&offset=0')) {
        return Promise.resolve(jsonResponse(page<CrawlRun>([run])));
      }
      if (url.endsWith('/runs/run-1')) {
        return Promise.resolve(jsonResponse(run));
      }
      if (url.endsWith('/runs/run-1/events?limit=100&offset=0')) {
        return Promise.resolve(jsonResponse(page<CrawlRunEvent>([event])));
      }
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<RunDetailPage />);

    expect(await screen.findByRole('heading', { name: 'run-1' })).toBeInTheDocument();
    expect(screen.getByText('Fetched official setup guide.')).toBeInTheDocument();
    expect(screen.getByText('fetch / page_fetched')).toBeInTheDocument();
    expect(screen.getByText('Discovered')).toBeInTheDocument();
    expect(screen.getByText('4')).toBeInTheDocument();
    expect(screen.getByText('Fetched')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.queryByText(EMPTY_STATE_COPY)).not.toBeInTheDocument();
  });

  it('renders an explicit API error instead of empty-state copy when runs fail to load', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('backend offline')));

    render(<RunDetailPage />);

    expect(await screen.findByRole('alert')).toHaveTextContent('Unable to load runs: backend offline');
    expect(screen.queryByText(EMPTY_STATE_COPY)).not.toBeInTheDocument();
  });
});
