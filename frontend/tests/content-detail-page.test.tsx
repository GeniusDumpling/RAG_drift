import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ContentChunk, ContentDetail, ContentListItem, CrawlRun, Page, RawPage, SourceSite } from '../src/api/types';
import { ContentDetailPage } from '../src/pages/ContentDetailPage';

const EMPTY_STATE_COPY = '暂无数据。请先启动后端并运行 demo seed 脚本。';

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
  item_type: 'post',
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
    if (url.endsWith('/contents?limit=100&offset=0&item_type=thread')) {
      return Promise.resolve(jsonResponse(page<ContentListItem>([])));
    }
    if (url.endsWith('/contents?limit=100&offset=0&item_type=post')) {
      return Promise.resolve(jsonResponse(page<ContentListItem>([contentItem])));
    }
    if (url.endsWith('/contents?limit=100&offset=0&item_type=comment')) {
      return Promise.resolve(jsonResponse(page<ContentListItem>([])));
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
  it('renders backend-shaped content detail with source, raw page, run callback, and chunk lineage', async () => {
    const onOpenRun = vi.fn();
    stubContentFetch();

    render(<ContentDetailPage onOpenRun={onOpenRun} />);

    expect(await screen.findByRole('heading', { name: 'Setup Guide' })).toBeInTheDocument();
    expect(screen.getByText('Example Docs')).toBeInTheDocument();
    expect(screen.getByText('Ops Writer')).toBeInTheDocument();
    expect(screen.getByText('parsed')).toBeInTheDocument();
    expect(screen.getByText('A concise setup guide.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '索引文本预览' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Cleaned Text' })).not.toBeInTheDocument();
    expect(screen.getByText('Install the package and disable telemetry.', { selector: 'pre' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Raw page 链接' })).toHaveAttribute(
      'href',
      'https://example.test/docs/setup?canonical=1',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Run 链接' }));
    expect(onOpenRun).toHaveBeenCalledTimes(1);
    expect(onOpenRun).toHaveBeenCalledWith('run-1');
    expect(screen.queryByRole('link', { name: 'Run 链接' })).not.toBeInTheDocument();
    expect(screen.getByText('Chunk 0')).toBeInTheDocument();
    expect(screen.getByText('Install the package and disable telemetry.', { selector: 'p' })).toBeInTheDocument();
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
    expect(screen.queryByRole('link', { name: 'Raw page 链接' })).not.toBeInTheDocument();
    expect(screen.getByText(`Raw page 链接不可用：${unsafeUrl}`)).toBeInTheDocument();
  });

  it('preserves and displays an externally selected content id that is absent from the loaded content page', async () => {
    const selectedItem: ContentListItem = {
      ...contentItem,
      id: 'content-selected',
      canonical_url: 'https://example.test/docs/selected',
      title: 'Selected Evidence Guide',
    };
    const selectedDetail: ContentDetail = {
      ...detail,
      ...selectedItem,
      raw_page: { ...rawPage, requested_url: selectedItem.canonical_url, final_url: selectedItem.canonical_url },
      chunks: [{ ...chunk, id: 'chunk-selected', content_item_id: selectedItem.id, display_text: 'Selected detail chunk.' }],
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/contents?limit=100&offset=0&item_type=thread')) {
        return Promise.resolve(jsonResponse(page<ContentListItem>([])));
      }
      if (url.endsWith('/contents?limit=100&offset=0&item_type=post')) {
        return Promise.resolve(jsonResponse(page<ContentListItem>([contentItem])));
      }
      if (url.endsWith('/contents?limit=100&offset=0&item_type=comment')) {
        return Promise.resolve(jsonResponse(page<ContentListItem>([])));
      }
      if (url.endsWith('/contents/content-selected')) {
        return Promise.resolve(jsonResponse(selectedDetail));
      }
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<ContentDetailPage selectedContentId={selectedItem.id} />);

    expect(await screen.findByRole('heading', { name: 'Selected Evidence Guide' })).toBeInTheDocument();
    const selector = screen.getByLabelText('内容') as HTMLSelectElement;
    expect(selector.tagName).toBe('SELECT');
    expect(selector.value).toBe(selectedItem.id);
    expect(Array.from(selector.options).map((option) => option.value)).toContain(selectedItem.id);
    expect(screen.getByRole('option', { name: /content-selected/ })).toBeInTheDocument();
    expect(screen.getByText('Selected detail chunk.', { selector: 'pre' })).toBeInTheDocument();
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([request]) => String(request).endsWith('/contents/content-selected'))).toBe(true);
    });
  });

  it('switches between comment and video description records', async () => {
    const videoItem: ContentListItem = {
      ...contentItem,
      id: 'video-description-1',
      item_type: 'video_description',
      title: 'Flight demonstration description',
    };
    const videoDetail: ContentDetail = {
      ...detail,
      ...videoItem,
      chunks: [{ ...chunk, id: 'video-description-chunk-1', content_item_id: videoItem.id, display_text: 'Video description evidence.' }],
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/contents?limit=100&offset=0&item_type=thread')) {
        return Promise.resolve(jsonResponse(page<ContentListItem>([])));
      }
      if (url.endsWith('/contents?limit=100&offset=0&item_type=post')) {
        return Promise.resolve(jsonResponse(page<ContentListItem>([contentItem])));
      }
      if (url.endsWith('/contents?limit=100&offset=0&item_type=comment')) {
        return Promise.resolve(jsonResponse(page<ContentListItem>([])));
      }
      if (url.endsWith('/contents?limit=100&offset=0&item_type=video_description')) {
        return Promise.resolve(jsonResponse(page<ContentListItem>([videoItem])));
      }
      if (url.endsWith('/contents/content-1')) {
        return Promise.resolve(jsonResponse(detail));
      }
      if (url.endsWith('/contents/video-description-1')) {
        return Promise.resolve(jsonResponse(videoDetail));
      }
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<ContentDetailPage />);

    expect(await screen.findByRole('heading', { name: 'Setup Guide' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('查看数据类型'), { target: { value: 'video_description' } });

    expect(await screen.findByRole('heading', { name: 'Flight demonstration description' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: '视频描述类数据' })).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([request]) => String(request).endsWith('/contents?limit=100&offset=0&item_type=video_description'))).toBe(true);
  });

  it('renders an explicit API error when content list loading fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('backend offline')));

    render(<ContentDetailPage />);

    expect(await screen.findByRole('alert')).toHaveTextContent('无法加载 Content 列表: backend offline');
    expect(screen.getByText('Content 详情暂不可用：API 请求失败。')).toBeInTheDocument();
    expect(screen.queryByText(EMPTY_STATE_COPY)).not.toBeInTheDocument();
  });

  it('renders the empty state when no content records are available', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/contents?limit=100&offset=0&item_type=thread')) {
        return Promise.resolve(jsonResponse(page<ContentListItem>([])));
      }
      if (url.endsWith('/contents?limit=100&offset=0&item_type=post')) {
        return Promise.resolve(jsonResponse(page<ContentListItem>([])));
      }
      if (url.endsWith('/contents?limit=100&offset=0&item_type=comment')) {
        return Promise.resolve(jsonResponse(page<ContentListItem>([])));
      }
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<ContentDetailPage />);

    expect(await screen.findByText(EMPTY_STATE_COPY)).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
  });
});
