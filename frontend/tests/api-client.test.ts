import { afterEach, describe, expect, it, vi } from 'vitest';

import { listSources } from '../src/api/client';

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

describe('API client base URL', () => {
  it('defaults to same-origin relative API requests for deployed static builds', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ items: [], total: 0, limit: 50, offset: 0 }));
    vi.stubGlobal('fetch', fetchMock);

    await listSources();

    expect(fetchMock).toHaveBeenCalledWith('/sources?limit=50&offset=0', undefined);
  });
});
