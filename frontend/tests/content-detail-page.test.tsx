import '@testing-library/jest-dom/vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ContentChunk, ContentDetail, ContentListItem, CrawlRun, Page, RawPage, SourceSite } from '../src/api/types';
import { ContentDetailPage } from '../src/pages/ContentDetailPage';

const EMPTY_STATE_COPY = 'No data loaded yet. Start the backend and run the demo seed script.';

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
  status: 'completed',
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
  final_url: 'https://example.test/docs/setup?canonical=1',
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
  chunk_metadata_json: { raw_page_id: 'raw-1' },
  qdrant_point_id: 'legacy-point-1',
  vector_backend: 'qdrant',
  vector_point_id: 'point-1',
  embedded_at: '2026-01-01T00:02:00Z',
  embed_status: 'embedded',
  embed_error: null,
  created_at: '2026-01-01T00:02:00Z',
  updated_at: '2026-01-01T00:02:00Z',
};

const contentItem: ContentListItem = {
  id: 'content-1',
  source_site_id: 'source-1',
  raw_page_id: 'raw-1',
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
  cleaned_text: 'Cleaned setup guide text.',
  raw_page: rawPage,
  source,
  crawl_run: run,
  chunks: [chunk],
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

function stubContentFetch(contentDetail: ContentDetail = detail) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith('/contents?limit=25&offset=0')) {
      return Promise.resolve(jsonResponse(page<ContentListItem>([contentItem])));
    }
    if (url.endsWith('/contents/content-1')) {
      return Promise.resolve(jsonResponse(contentDetail));
    }
    return Promise.reject(new Error(`Unexpected URL: ${url}`));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('ContentDetailPage', () => {
  it('renders content detail with source, raw page, run, and chunk lineage', async () => {
    stubContentFetch();

    render(<ContentDetailPage />);

    expect(await screen.findByRole('heading', { name: 'Setup Guide' })).toBeInTheDocument();
    expect(screen.getByText('Example Docs')).toBeInTheDocument();
    expect(screen.getByText('Ops Writer')).toBeInTheDocument();
    expect(screen.getByText('parsed')).toBeInTheDocument();
    expect(screen.getByText('A concise setup guide.')).toBeInTheDocument();
    expect(screen.getByText('Cleaned setup guide text.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Raw page link' })).toHaveAttribute(
      'href',
      'https://example.test/docs/setup?canonical=1',
    );
    expect(screen.getByRole('link', { name: 'Run link' })).toHaveAttribute('href', '#run-run-1');
    expect(screen.getByText('Chunk 0')).toBeInTheDocument();
    expect(screen.getByText('Install the package and disable telemetry.')).toBeInTheDocument();
    expect(screen.getByText('qdrant')).toBeInTheDocument();
    expect(screen.getByText('point-1')).toBeInTheDocument();
    expect(screen.queryByText(EMPTY_STATE_COPY)).not.toBeInTheDocument();
  });

  it('renders an unsafe raw page URL as disabled text instead of a link', async () => {
    const unsafeUrl = 'javascript:alert(1)';
    stubContentFetch({
      ...detail,
      raw_page: { ...rawPage, final_url: null, requested_url: unsafeUrl },
    });

    render(<ContentDetailPage />);

    expect(await screen.findByRole('heading', { name: 'Setup Guide' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Raw page link' })).not.toBeInTheDocument();
    expect(screen.getByText(`Raw page link unavailable: ${unsafeUrl}`)).toBeInTheDocument();
  });

  it('renders an explicit API error when content list loading fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('backend offline')));

    render(<ContentDetailPage />);

    expect(await screen.findByRole('alert')).toHaveTextContent('Unable to load content list: backend offline');
    expect(screen.getByText('Content detail unavailable while the API request is failing.')).toBeInTheDocument();
    expect(screen.queryByText(EMPTY_STATE_COPY)).not.toBeInTheDocument();
  });

  it('renders the empty state when no content records are available', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/contents?limit=25&offset=0')) {
        return Promise.resolve(jsonResponse(page<ContentListItem>([])));
      }
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<ContentDetailPage />);

    expect(await screen.findByText(EMPTY_STATE_COPY)).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  });
});
