import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import App from '../src/App';

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

describe('App database navigation', () => {
  it('opens the read-only Database page from the main navigation', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/sources?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 50, offset: 0 }));
      }
      if (url.endsWith('/jobs?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 50, offset: 0 }));
      }
      if (url.endsWith('/runs?limit=50&offset=0')) {
        return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 50, offset: 0 }));
      }
      if (url.endsWith('/contents?limit=10&offset=0')) {
        return Promise.resolve(jsonResponse({ items: [], total: 0, limit: 10, offset: 0 }));
      }
      if (url.endsWith('/database/overview')) {
        return Promise.resolve(
          jsonResponse({
            postgres: {
              status: 'ok',
              database_name: 'intelligence_rag',
              server_version: 'PostgreSQL 16',
              checked_at: '2026-06-22T12:00:00Z',
              table_counts: [],
              latest_run: null,
              latest_search: null,
              chunk_vector_status: [],
            },
            qdrant: {
              status: 'unavailable',
              collection: 'content_chunks_v1',
              points_count: null,
              vector_size: null,
              distance: null,
              error: 'Qdrant request failed',
            },
            reconciliation: {
              postgres_qdrant_chunk_count: 0,
              qdrant_points_count: null,
              status: 'unknown',
            },
          }),
        );
      }
      if (url.endsWith('/database/tables')) {
        return Promise.resolve(jsonResponse({ items: [], total: 0 }));
      }
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    fireEvent.click(screen.getByRole('button', { name: '数据库' }));

    expect(await screen.findByRole('heading', { name: '数据库' })).toBeInTheDocument();
    expect(screen.getByText('只读展示，不支持 SQL 执行或写入操作。')).toBeInTheDocument();
  });
});
