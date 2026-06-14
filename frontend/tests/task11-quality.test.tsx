import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import type {
  ContentDetail,
  ContentListItem,
  CrawlJob,
  CrawlRun,
  CrawlRunEvent,
  Page,
  SourceSite,
} from '../src/api/types';
import { ContentDetailPage } from '../src/pages/ContentDetailPage';
import { DashboardPage } from '../src/pages/DashboardPage';
import { RunDetailPage } from '../src/pages/RunDetailPage';
import { SourcesPage } from '../src/pages/SourcesPage';

const apiMocks = vi.hoisted(() => ({
  getContent: vi.fn(),
  getRun: vi.fn(),
  getRunEvents: vi.fn(),
  listContents: vi.fn(),
  listJobs: vi.fn(),
  listRuns: vi.fn(),
  listSources: vi.fn(),
  triggerJob: vi.fn(),
}));

vi.mock('../src/api/client', () => apiMocks);

const EMPTY_STATE_COPY = 'No data loaded yet. Start the backend and run the demo seed script.';
const NOW = '2025-01-01T00:00:00.000Z';

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
};

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function page<T>(items: T[]): Page<T> {
  return {
    items,
    total: items.length,
    limit: Math.max(items.length, 25),
    offset: 0,
  };
}

function makeSource(id = 'source-1'): SourceSite {
  return {
    id,
    name: `Source ${id}`,
    site_type: 'official',
    base_url: `https://${id}.example.test`,
    allowed_domains: [`${id}.example.test`],
    fetch_mode: 'http',
    default_language: 'en',
    active: true,
    config_json: {},
    created_at: NOW,
    updated_at: NOW,
  };
}

function makeJob(id = 'job-1', sourceSiteId = 'source-1'): CrawlJob {
  return {
    id,
    source_site_id: sourceSiteId,
    name: `Job ${id}`,
    trigger_mode: 'manual',
    cron_expr: null,
    seed_config_json: {},
    parser_profile: 'article',
    max_pages: 10,
    enabled: true,
    agent_policy_json: {},
    last_run_at: null,
    next_run_at: null,
    created_at: NOW,
    updated_at: NOW,
  };
}

function makeRun(id = 'run-1', status = 'completed'): CrawlRun {
  return {
    id,
    source_site_id: 'source-1',
    crawl_job_id: 'job-1',
    trigger_type: 'manual',
    execution_mode: 'single',
    seed_url: 'https://source-1.example.test/start',
    status,
    started_at: NOW,
    finished_at: NOW,
    discovered_count: 1,
    fetched_count: 1,
    parsed_count: 1,
    extracted_count: 1,
    deduped_count: 0,
    chunked_count: 1,
    embedded_count: 1,
    error_count: 0,
    config_snapshot_json: {},
    error_message: null,
    created_at: NOW,
  };
}

function makeRunEvent(id = 'event-1', crawlRunId = 'run-1'): CrawlRunEvent {
  return {
    id,
    crawl_run_id: crawlRunId,
    stage: 'fetch',
    level: 'info',
    event_type: 'started',
    message: 'Fetch started',
    related_url: null,
    related_raw_page_id: null,
    related_content_item_id: null,
    counters_json: {},
    agent_trace_json: {},
    created_at: NOW,
  };
}

function makeContentItem(id = 'content-1'): ContentListItem {
  return {
    id,
    source_site_id: 'source-1',
    item_type: 'article',
    canonical_url: `https://source-1.example.test/${id}`,
    title: `Content ${id}`,
    author_name: 'Analyst',
    published_at: NOW,
    fetched_at: NOW,
    summary_text: 'Summary',
    tags: ['tag'],
    extraction_confidence: 0.9,
  };
}

function makeContentDetail(id = 'content-1'): ContentDetail {
  const item = makeContentItem(id);
  return {
    ...item,
    raw_page: {
      id: 'raw-page-1',
      source_site_id: item.source_site_id,
      crawl_run_id: 'run-1',
      requested_url: item.canonical_url,
      final_url: item.canonical_url,
      http_status: 200,
      content_type: 'text/html',
      response_headers_json: {},
      raw_html: '<p>Raw</p>',
      raw_text: 'Raw text',
      raw_json: {},
      fetched_at: NOW,
      fetch_error: null,
      parser_profile: 'article',
      extraction_method: 'agent',
      extraction_confidence: 0.9,
      parse_status: 'parsed',
      parse_error: null,
      body_hash: 'hash',
      created_at: NOW,
    },
    source: makeSource(item.source_site_id),
    crawl_run: makeRun('run-1'),
    chunks: [],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.getContent.mockResolvedValue(makeContentDetail());
  apiMocks.getRun.mockResolvedValue(makeRun());
  apiMocks.getRunEvents.mockResolvedValue(page<CrawlRunEvent>([]));
  apiMocks.listContents.mockResolvedValue(page<ContentListItem>([]));
  apiMocks.listJobs.mockResolvedValue(page<CrawlJob>([]));
  apiMocks.listRuns.mockResolvedValue(page<CrawlRun>([]));
  apiMocks.listSources.mockResolvedValue(page<SourceSite>([]));
});

afterEach(() => {
  cleanup();
});

describe('Task 11 frontend quality fixes', () => {
  test('keeps selected run id in the selector when the selected run is absent from the loaded run page', async () => {
    const listedRun = makeRun('run-listed', 'running');
    const selectedRun = makeRun('run-selected', 'completed');
    apiMocks.listRuns.mockResolvedValue(page([listedRun]));
    apiMocks.getRun.mockImplementation((id: string) => Promise.resolve(id === selectedRun.id ? selectedRun : listedRun));
    apiMocks.getRunEvents.mockResolvedValue(page([makeRunEvent('event-selected', selectedRun.id)]));

    render(<RunDetailPage selectedRunId={selectedRun.id} />);

    await screen.findByRole('heading', { name: selectedRun.id });
    await screen.findByRole('option', { name: new RegExp(listedRun.id) });

    const selector = screen.getByLabelText('Run') as HTMLSelectElement;
    expect(selector.tagName).toBe('SELECT');
    expect(selector.value).toBe(selectedRun.id);
    expect(Array.from(selector.options).map((option) => option.value)).toContain(selectedRun.id);
  });

  test('shows a sources loading status before the empty state', async () => {
    const sources = deferred<Page<SourceSite>>();
    const jobs = deferred<Page<CrawlJob>>();
    apiMocks.listSources.mockReturnValue(sources.promise);
    apiMocks.listJobs.mockReturnValue(jobs.promise);

    render(<SourcesPage />);

    expect(screen.getByRole('status').textContent).toContain('Loading sources data');
    expect(screen.queryAllByText(EMPTY_STATE_COPY)).toHaveLength(0);

    await act(async () => {
      sources.resolve(page<SourceSite>([]));
      jobs.resolve(page<CrawlJob>([]));
      await Promise.all([sources.promise, jobs.promise]);
    });

    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    expect(screen.getByText(EMPTY_STATE_COPY)).toBeTruthy();
  });

  test('shows a run loading status before the empty state', async () => {
    const runs = deferred<Page<CrawlRun>>();
    apiMocks.listRuns.mockReturnValue(runs.promise);

    render(<RunDetailPage />);

    expect(screen.getByRole('status').textContent).toContain('Loading run data');
    expect(screen.queryAllByText(EMPTY_STATE_COPY)).toHaveLength(0);

    await act(async () => {
      runs.resolve(page<CrawlRun>([]));
      await runs.promise;
    });

    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    expect(screen.getAllByText(EMPTY_STATE_COPY).length).toBeGreaterThan(0);
  });

  test('shows a content loading status before the empty state', async () => {
    const contents = deferred<Page<ContentListItem>>();
    apiMocks.listContents.mockReturnValue(contents.promise);

    render(<ContentDetailPage />);

    expect(screen.getByRole('status').textContent).toContain('Loading content data');
    expect(screen.queryAllByText(EMPTY_STATE_COPY)).toHaveLength(0);

    await act(async () => {
      contents.resolve(page<ContentListItem>([]));
      await contents.promise;
    });

    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    expect(screen.getByText(EMPTY_STATE_COPY)).toBeTruthy();
  });

  test('shows a dashboard loading status before the empty state', async () => {
    const sources = deferred<Page<SourceSite>>();
    const jobs = deferred<Page<CrawlJob>>();
    const runs = deferred<Page<CrawlRun>>();
    const contents = deferred<Page<ContentListItem>>();
    apiMocks.listSources.mockReturnValue(sources.promise);
    apiMocks.listJobs.mockReturnValue(jobs.promise);
    apiMocks.listRuns.mockReturnValue(runs.promise);
    apiMocks.listContents.mockReturnValue(contents.promise);

    render(<DashboardPage />);

    expect(screen.getByRole('status').textContent).toContain('Loading dashboard data');
    expect(screen.queryAllByText(EMPTY_STATE_COPY)).toHaveLength(0);

    await act(async () => {
      sources.resolve(page<SourceSite>([]));
      jobs.resolve(page<CrawlJob>([]));
      runs.resolve(page<CrawlRun>([]));
      contents.resolve(page<ContentListItem>([]));
      await Promise.all([sources.promise, jobs.promise, runs.promise, contents.promise]);
    });

    await waitFor(() => expect(screen.queryByRole('status')).toBeNull());
    expect(screen.getAllByText(EMPTY_STATE_COPY).length).toBeGreaterThan(0);
  });
});
