import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { App } from '../src/App';
import type {
  ContentDetail,
  ContentListItem,
  CrawlRun,
  EvidenceObject,
  Page,
  RawPage,
  SearchQueryRead,
  SearchResponse,
  SourceSite,
} from '../src/api/types';

const NOW = '2026-01-01T00:00:00Z';

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
  created_at: NOW,
  updated_at: NOW,
};

const run: CrawlRun = {
  id: 'run-1',
  source_site_id: source.id,
  crawl_job_id: 'job-1',
  trigger_type: 'manual',
  execution_mode: 'foreground',
  seed_url: 'https://example.test/docs/start',
  status: 'success',
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

const listedContent: ContentListItem = {
  id: 'content-listed',
  source_site_id: source.id,
  item_type: 'doc_page',
  canonical_url: 'https://example.test/docs/listed',
  title: 'Listed Content',
  author_name: 'Ops Writer',
  published_at: NOW,
  fetched_at: NOW,
  summary_text: 'Listed content summary.',
  tags: ['listed'],
  extraction_confidence: 0.9,
};

const selectedContent: ContentListItem = {
  id: 'content-selected',
  source_site_id: source.id,
  item_type: 'article',
  canonical_url: 'https://example.test/docs/selected',
  title: 'Selected Evidence Detail',
  author_name: 'Analyst',
  published_at: NOW,
  fetched_at: NOW,
  summary_text: 'Selected content loaded from evidence navigation.',
  tags: ['evidence'],
  extraction_confidence: 0.95,
};

const selectedRawPage: RawPage = {
  id: 'raw-selected',
  source_site_id: source.id,
  crawl_run_id: run.id,
  requested_url: selectedContent.canonical_url,
  final_url: selectedContent.canonical_url,
  http_status: 200,
  content_type: 'text/html',
  response_headers_json: {},
  raw_html: '<html>Selected evidence detail</html>',
  raw_text: 'Selected evidence raw text.',
  raw_json: {},
  fetched_at: NOW,
  fetch_error: null,
  parser_profile: 'article',
  extraction_method: 'agent',
  extraction_confidence: 0.95,
  parse_status: 'parsed',
  parse_error: null,
  body_hash: 'hash-selected',
  created_at: NOW,
};

const selectedDetail: ContentDetail = {
  ...selectedContent,
  raw_page: selectedRawPage,
  source,
  crawl_run: run,
  chunks: [
    {
      id: 'chunk-selected',
      content_item_id: selectedContent.id,
      chunk_index: 0,
      char_start: 0,
      char_end: 42,
      display_text: 'Selected evidence chunk text.',
      embed_text: 'Selected evidence chunk text.',
      token_count: 4,
      chunk_metadata_json: {},
      qdrant_point_id: null,
      vector_backend: 'qdrant',
      vector_point_id: 'point-selected',
      embedded_at: NOW,
      embed_status: 'embedded',
      embed_error: null,
      created_at: NOW,
      updated_at: NOW,
    },
  ],
};

const queryRecord: SearchQueryRead = {
  id: 'query-1',
  raw_query: 'telemetry disable',
  filter_json: {},
  mode: 'search',
  top_k: 10,
  used_agent: false,
  optimized_query_text: 'telemetry disable',
  keyword_terms_json: ['telemetry', 'disable'],
  entity_hints_json: [],
  time_hints_json: {},
  result_summary_json: { kept: 1 },
  query_trace_json: {},
  result_count: 1,
  created_at: NOW,
};

const evidence: EvidenceObject = {
  chunk_id: 'chunk-selected',
  content_item_id: selectedContent.id,
  raw_page_id: selectedRawPage.id,
  source_site_id: source.id,
  title: 'Search Evidence Result',
  snippet: 'Evidence result points to a content item outside the first content page.',
  canonical_url: selectedContent.canonical_url,
  source_site_name: source.name,
  author_name: 'Analyst',
  published_at: NOW,
  item_type: 'article',
  score: 0.98,
  vector_score: 0.97,
  keyword_score: 0.8,
  matched_by: 'hybrid',
  thread_summary: null,
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

describe('App search evidence content navigation', () => {
  it('opens the selected evidence content item in Content Detail and fetches that item', async () => {
    const searchResponse: SearchResponse = { query: queryRecord, evidence: [evidence] };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/sources?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse(page<SourceSite>([])));
      }
      if (url.endsWith('/jobs?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse(page([])));
      }
      if (url.endsWith('/runs?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse(page([])));
      }
      if (url.endsWith('/contents?limit=10&offset=0')) {
        return Promise.resolve(jsonResponse(page<ContentListItem>([], 10)));
      }
      if (url.endsWith('/search') && init?.method === 'POST') {
        return Promise.resolve(jsonResponse(searchResponse));
      }
      if (url.endsWith('/contents?limit=100&offset=0')) {
        return Promise.resolve(jsonResponse(page<ContentListItem>([listedContent], 25)));
      }
      if (url.endsWith(`/contents/${selectedContent.id}`)) {
        return Promise.resolve(jsonResponse(selectedDetail));
      }
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    const primaryNav = screen.getByRole('navigation', { name: '主导航 Primary' });
    fireEvent.click(within(primaryNav).getByRole('button', { name: '检索问答' }));
    fireEvent.change(screen.getByLabelText('查询'), { target: { value: 'telemetry disable' } });
    fireEvent.click(within(screen.getByRole('main')).getByRole('button', { name: '检索' }));

    expect(await screen.findByText('Search Evidence Result')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: `打开内容 ${selectedContent.id}` }));

    expect(await screen.findByRole('heading', { name: selectedContent.title })).toBeInTheDocument();
    expect(screen.getByText('Selected evidence chunk text.', { selector: 'pre' })).toBeInTheDocument();
    expect(within(primaryNav).getByRole('button', { name: '内容详情' })).toHaveAttribute('aria-current', 'page');
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([request]) => String(request).endsWith(`/contents/${selectedContent.id}`))).toBe(
        true,
      );
    });
  });
});
