import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { App } from '../src/App';
import type { ContentChunk, ContentDetail, ContentListItem, CrawlRun, Page, RawPage, SourceSite } from '../src/api/types';

const source: SourceSite = {
  id: 'source-1',
  name: 'Example Docs',
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

const run: CrawlRun = {
  id: 'run-1',
  source_site_id: 'source-1',
  crawl_job_id: 'job-1',
  trigger_type: 'manual',
  execution_mode: 'foreground',
  seed_url: 'https://example.test/docs/start',
  status: 'success',
  started_at: '2026-01-01T00:00:00Z',
  finished_at: '2026-01-01T00:05:00Z',
  discovered_count: 3,
  fetched_count: 2,
  parsed_count: 2,
  extracted_count: 1,
  deduped_count: 0,
  chunked_count: 1,
  embedded_count: 1,
  error_count: 0,
  config_snapshot_json: {},
  error_message: null,
  created_at: '2026-01-01T00:00:00Z',
};

const rawPage: RawPage = {
  id: 'raw-1',
  source_site_id: 'source-1',
  crawl_run_id: 'run-1',
  requested_url: 'https://example.test/docs/setup',
  final_url: 'https://example.test/docs/setup',
  http_status: 200,
  content_type: 'text/html',
  response_headers_json: {},
  raw_html: '<html>Setup guide</html>',
  raw_text: 'Raw setup guide text.',
  raw_json: {},
  fetched_at: '2026-01-01T00:01:00Z',
  fetch_error: null,
  parser_profile: 'docs',
  extraction_method: 'agent',
  extraction_confidence: 0.91,
  parse_status: 'parsed',
  parse_error: null,
  body_hash: 'hash-1',
  created_at: '2026-01-01T00:01:00Z',
};

const chunk: ContentChunk = {
  id: 'chunk-1',
  content_item_id: 'content-1',
  chunk_index: 0,
  char_start: 0,
  char_end: 42,
  display_text: 'Install the package and disable telemetry.',
  embed_text: 'Install the package and disable telemetry.',
  token_count: 7,
  chunk_metadata_json: {},
  qdrant_point_id: null,
  vector_backend: null,
  vector_point_id: null,
  embedded_at: null,
  embed_status: 'pending',
  embed_error: null,
  created_at: '2026-01-01T00:02:00Z',
  updated_at: '2026-01-01T00:02:00Z',
};

const contentItem: ContentListItem = {
  id: 'content-1',
  source_site_id: 'source-1',
  item_type: 'doc_page',
  canonical_url: 'https://example.test/docs/setup',
  title: 'Setup Guide',
  author_name: 'Ops Writer',
  published_at: '2025-12-01T00:00:00Z',
  fetched_at: '2026-01-01T00:01:00Z',
  summary_text: 'A concise setup guide.',
  tags: ['setup', 'telemetry'],
  extraction_confidence: 0.93,
};

const detail: ContentDetail = {
  ...contentItem,
  raw_page: rawPage,
  source,
  crawl_run: run,
  chunks: [chunk],
};

function page<T>(items: T[], limit = 50): Page<T> {
  return { items, total: items.length, limit, offset: 0 };
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

describe('App run navigation', () => {
  it('exposes primary navigation semantics and the current page', () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/sources?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse(page<SourceSite>([])));
      }
      if (url.endsWith('/jobs?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse(page([])));
      }
      if (url.endsWith('/runs?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse(page<CrawlRun>([])));
      }
      if (url.endsWith('/contents?limit=10&offset=0')) {
        return Promise.resolve(jsonResponse(page<ContentListItem>([], 10)));
      }
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    const primaryNav = screen.getByRole('navigation', { name: 'Primary' });
    const dashboardButton = within(primaryNav).getByRole('button', { name: 'Dashboard' });
    const searchButton = within(primaryNav).getByRole('button', { name: 'Search' });

    expect(dashboardButton).toHaveAttribute('aria-current', 'page');
    expect(searchButton).not.toHaveAttribute('aria-current');

    fireEvent.click(searchButton);

    expect(searchButton).toHaveAttribute('aria-current', 'page');
    expect(dashboardButton).not.toHaveAttribute('aria-current');
  });

  it('opens the selected run detail from the content detail run link', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/sources?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse(page<SourceSite>([])));
      }
      if (url.endsWith('/jobs?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse(page([])));
      }
      if (url.endsWith('/runs?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse(page<CrawlRun>([])));
      }
      if (url.endsWith('/contents?limit=10&offset=0')) {
        return Promise.resolve(jsonResponse(page<ContentListItem>([], 10)));
      }
      if (url.endsWith('/contents?limit=25&offset=0')) {
        return Promise.resolve(jsonResponse(page<ContentListItem>([contentItem], 25)));
      }
      if (url.endsWith('/contents/content-1')) {
        return Promise.resolve(jsonResponse(detail));
      }
      if (url.endsWith('/runs?limit=25&offset=0')) {
        return Promise.resolve(jsonResponse(page<CrawlRun>([], 25)));
      }
      if (url.endsWith('/runs/run-1')) {
        return Promise.resolve(jsonResponse(run));
      }
      if (url.endsWith('/runs/run-1/events?limit=100&offset=0')) {
        return Promise.resolve(jsonResponse(page([], 100)));
      }
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    fireEvent.click(screen.getByRole('button', { name: 'Content' }));
    expect(await screen.findByRole('heading', { name: 'Setup Guide' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Run link' }));

    expect(await screen.findByRole('heading', { name: 'run-1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Runs' })).toHaveClass('active');
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([request]) => String(request).endsWith('/runs/run-1'))).toBe(true);
    });
  });
});
