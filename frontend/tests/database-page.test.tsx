import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DatabasePage } from '../src/pages/DatabasePage';

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

const overview = {
  postgres: {
    status: 'ok',
    database_name: 'intelligence_rag',
    server_version: 'PostgreSQL 16',
    checked_at: '2026-06-22T12:00:00Z',
    table_counts: [
      { table_name: 'source_sites', count: 3 },
      { table_name: 'content_chunks', count: 67 },
    ],
    latest_run: {
      id: 'run-1',
      status: 'success',
      created_at: '2026-06-22T12:00:00Z',
      parsed_count: 61,
      chunked_count: 0,
      embedded_count: 67,
      error_count: 0,
    },
    latest_search: {
      id: 'query-1',
      raw_query: '报送 固件',
      mode: 'search',
      result_count: 3,
      created_at: '2026-06-22T12:01:00Z',
    },
    chunk_vector_status: [{ embed_status: 'success', vector_backend: 'qdrant', count: 67 }],
  },
  qdrant: {
    status: 'ok',
    collection: 'content_chunks_v1',
    points_count: 67,
    vector_size: 384,
    distance: 'Cosine',
    error: null,
  },
  reconciliation: {
    postgres_qdrant_chunk_count: 67,
    qdrant_points_count: 67,
    status: 'matched',
  },
};

const tables = {
  items: [
    {
      table_name: 'content_items',
      label: '内容项 content_items',
      description: '归一化后的 thread/comment/article 等展示与引用单位。',
      default_sort: 'created_at desc',
      preview_columns: ['id', 'item_type', 'title'],
      searchable_columns: ['title', 'canonical_url', 'cleaned_text'],
    },
    {
      table_name: 'content_chunks',
      label: '内容切片 content_chunks',
      description: 'RAG 检索单位，记录 embedding 和 Qdrant point 状态。',
      default_sort: 'created_at desc',
      preview_columns: ['id', 'embed_status', 'vector_backend'],
      searchable_columns: ['display_text', 'embed_text'],
    },
  ],
  total: 2,
};

const itemRows = {
  items: [
    {
      id: 'item-1',
      table_name: 'content_items',
      preview: { id: 'item-1', item_type: 'thread', title: 'Telemetry firmware report' },
      detail: { cleaned_text: { cleaned_text_preview: 'Telemetry firmware report cleaned text' } },
      related: { raw_page_id: 'raw-1', crawl_run_id: 'run-1' },
    },
  ],
  total: 1,
  limit: 50,
  offset: 0,
};

const chunkRows = {
  items: [
    {
      id: 'chunk-1',
      table_name: 'content_chunks',
      preview: { id: 'chunk-1', embed_status: 'success', vector_backend: 'qdrant' },
      detail: { embed_text: { embed_text_preview: 'Telemetry firmware report embed text' } },
      related: { content_item_id: 'item-1', content_title: 'Telemetry firmware report' },
    },
  ],
  total: 1,
  limit: 50,
  offset: 0,
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('DatabasePage', () => {
  it('renders PostgreSQL/Qdrant overview, table rows, row detail, and psql handbook', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/database/overview')) {
        return Promise.resolve(jsonResponse(overview));
      }
      if (url.endsWith('/database/tables')) {
        return Promise.resolve(jsonResponse(tables));
      }
      if (url.endsWith('/database/tables/content_items/rows?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse(itemRows));
      }
      if (url.endsWith('/database/tables/content_chunks/rows?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse(chunkRows));
      }
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<DatabasePage />);

    expect(await screen.findByRole('heading', { name: 'PostgreSQL 数据库展示 Database' })).toBeInTheDocument();
    expect(screen.getByText('intelligence_rag')).toBeInTheDocument();
    expect(screen.getByText('content_chunks_v1')).toBeInTheDocument();
    expect(screen.getByText('matched')).toBeInTheDocument();
    expect(screen.getByText('success / qdrant')).toBeInTheDocument();
    expect(await screen.findByText('Telemetry firmware report')).toBeInTheDocument();

    const handbook = screen.getByRole('region', { name: 'psql 使用手册' });
    expect(within(handbook).getByText(/psql -h 127\.0\.0\.1 -p 54329/)).toBeInTheDocument();
    expect(within(handbook).getByText(/select embed_status, vector_backend/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('选择表'), { target: { value: 'content_chunks' } });
    expect(await screen.findByText('chunk-1')).toBeInTheDocument();
    expect(screen.getByText('Telemetry firmware report embed text')).toBeInTheDocument();
  });

  it('passes keyword search to the selected table rows endpoint', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/database/overview')) {
        return Promise.resolve(jsonResponse(overview));
      }
      if (url.endsWith('/database/tables')) {
        return Promise.resolve(jsonResponse(tables));
      }
      if (url.endsWith('/database/tables/content_items/rows?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse(itemRows));
      }
      if (url.endsWith('/database/tables/content_items/rows?limit=50&offset=0&q=firmware')) {
        return Promise.resolve(jsonResponse(itemRows));
      }
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<DatabasePage />);

    await screen.findByText('Telemetry firmware report');
    fireEvent.change(screen.getByLabelText('表内搜索'), { target: { value: 'firmware' } });
    fireEvent.click(screen.getByRole('button', { name: '搜索表数据' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/database/tables/content_items/rows?limit=50&offset=0&q=firmware'),
        undefined,
      );
    });
  });

  it('renders explicit error state when database APIs fail', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('backend offline')));

    render(<DatabasePage />);

    expect(await screen.findByRole('alert')).toHaveTextContent('无法加载数据库展示数据: backend offline');
    expect(screen.getByText('数据库展示暂不可用。')).toBeInTheDocument();
  });
});
