import '@testing-library/jest-dom/vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SearchPage } from '../src/pages/SearchPage';
import type { AnswerResponse, EvidenceObject, SearchQueryRead, SearchResponse } from '../src/api/types';

const EMPTY_STATE_COPY = '暂无数据。请先启动后端并运行 demo seed 脚本。';

const queryRecord: SearchQueryRead = {
  id: 'query-1',
  raw_query: 'telemetry disable',
  filter_json: {},
  mode: 'search',
  top_k: 10,
  used_agent: true,
  optimized_query_text: 'disable telemetry settings',
  keyword_terms_json: ['telemetry', 'disable'],
  entity_hints_json: ['device'],
  time_hints_json: {},
  result_summary_json: { retrieval: 'hybrid', kept: 1 },
  query_trace_json: { stages: ['optimize', 'retrieve'] },
  result_count: 1,
  created_at: '2026-01-01T00:00:00Z',
};

const evidence: EvidenceObject = {
  chunk_id: 'chunk-1',
  content_item_id: 'content-1',
  raw_page_id: 'raw-1',
  source_site_id: 'source-1',
  title: 'Disable telemetry guide',
  snippet: 'Follow the device settings path to disable telemetry.',
  canonical_url: 'https://example.test/docs/telemetry',
  source_site_name: 'Example Docs',
  author_name: 'Ops Writer',
  published_at: '2025-12-01T00:00:00Z',
  item_type: 'doc_page',
  score: 0.91,
  vector_score: 0.88,
  keyword_score: 0.72,
  matched_by: 'hybrid',
  thread_summary: null,
};

const updatedEvidence: EvidenceObject = {
  ...evidence,
  chunk_id: 'chunk-2',
  content_item_id: 'content-2',
  raw_page_id: 'raw-2',
  source_site_id: 'source-2',
  title: 'Fleet telemetry policy',
  snippet: 'Fleet devices apply telemetry policy changes after synchronization.',
  canonical_url: 'https://example.test/docs/fleet-telemetry',
};

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

describe('SearchPage', () => {
  it('renders search controls and evidence area', () => {
    render(<SearchPage />);

    expect(screen.getByLabelText('查询')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '检索' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成答案' })).toBeInTheDocument();
    expect(screen.getByText('证据')).toBeInTheDocument();
  });

  it('renders evidence card and query trace after a successful search', async () => {
    const response: SearchResponse = { query: queryRecord, evidence: [evidence] };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(response));
    vi.stubGlobal('fetch', fetchMock);

    render(<SearchPage initialQuery="telemetry disable" />);
    fireEvent.click(screen.getByRole('button', { name: '检索' }));

    expect(await screen.findByRole('heading', { name: '查询追踪' })).toBeInTheDocument();
    expect(screen.getByText('Disable telemetry guide')).toBeInTheDocument();
    expect(screen.getByText('Follow the device settings path to disable telemetry.')).toBeInTheDocument();
    expect(screen.getByText('disable telemetry settings')).toBeInTheDocument();
    expect(screen.getByText('content-1')).toBeInTheDocument();
    expect(screen.getByText('source-1')).toBeInTheDocument();
    expect(screen.queryByText(EMPTY_STATE_COPY)).not.toBeInTheDocument();

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(String(init?.body))).toMatchObject({
      query: 'telemetry disable',
      mode: 'search',
      top_k: 10,
    });
  });

  it('clears stale search results and shows loading status during a pending second search', async () => {
    const pendingSearch = deferred<Response>();
    const firstResponse: SearchResponse = { query: queryRecord, evidence: [evidence] };
    const secondResponse: SearchResponse = {
      query: { ...queryRecord, id: 'query-2', raw_query: 'fleet telemetry', result_count: 1 },
      evidence: [updatedEvidence],
    };
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(firstResponse)).mockReturnValueOnce(pendingSearch.promise);
    vi.stubGlobal('fetch', fetchMock);

    render(<SearchPage initialQuery="telemetry disable" />);
    fireEvent.click(screen.getByRole('button', { name: '检索' }));

    expect(await screen.findByText('Disable telemetry guide')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('查询'), { target: { value: 'fleet telemetry' } });
    fireEvent.click(screen.getByRole('button', { name: '检索' }));

    expect(await screen.findByRole('status')).toHaveTextContent('正在检索...');
    expect(screen.queryByText('Disable telemetry guide')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '查询追踪' })).not.toBeInTheDocument();
    expect(screen.queryByText(EMPTY_STATE_COPY)).not.toBeInTheDocument();

    await act(async () => {
      pendingSearch.resolve(jsonResponse(secondResponse));
      await pendingSearch.promise;
    });

    expect(await screen.findByText('Fleet telemetry policy')).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.queryByText('Disable telemetry guide')).not.toBeInTheDocument();
  });

  it('renders answer draft and supporting evidence after a successful answer request', async () => {
    const response: AnswerResponse = {
      query: { ...queryRecord, mode: 'answer' },
      answer: 'Telemetry can be disabled from the device settings privacy panel.',
      supporting_evidence: [evidence],
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(response));
    vi.stubGlobal('fetch', fetchMock);

    render(<SearchPage initialQuery="telemetry disable" />);
    fireEvent.click(screen.getByRole('button', { name: '生成答案' }));

    expect(await screen.findByRole('heading', { name: '答案' })).toBeInTheDocument();
    expect(screen.getByText('Telemetry can be disabled from the device settings privacy panel.')).toBeInTheDocument();
    expect(screen.getByText('Disable telemetry guide')).toBeInTheDocument();
    expect(screen.getByText('Follow the device settings path to disable telemetry.')).toBeInTheDocument();

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(String(init?.body))).toMatchObject({
      query: 'telemetry disable',
      mode: 'answer',
      top_k: 10,
    });
  });

  it('clamps Top K to 1..50 and rejects a blank value without sending top_k 0', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ query: queryRecord, evidence: [evidence] }));
    vi.stubGlobal('fetch', fetchMock);

    render(<SearchPage initialQuery="telemetry disable" />);
    const topKInput = screen.getByLabelText('结果数') as HTMLInputElement;

    fireEvent.change(topKInput, { target: { value: '0' } });
    expect(topKInput).toHaveValue(1);

    fireEvent.change(topKInput, { target: { value: '99' } });
    expect(topKInput).toHaveValue(50);

    fireEvent.change(topKInput, { target: { value: '' } });
    expect(topKInput.value).toBe('');

    fireEvent.click(screen.getByRole('button', { name: '检索' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('结果数必须在 1 到 50 之间。');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('clears stale answer state when submitting a blank query after a successful answer', async () => {
    const response: AnswerResponse = {
      query: { ...queryRecord, mode: 'answer' },
      answer: 'Telemetry can be disabled from the device settings privacy panel.',
      supporting_evidence: [evidence],
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(response));
    vi.stubGlobal('fetch', fetchMock);

    render(<SearchPage initialQuery="telemetry disable" />);
    fireEvent.click(screen.getByRole('button', { name: '生成答案' }));

    expect(await screen.findByRole('heading', { name: '答案' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '查询追踪' })).toBeInTheDocument();
    expect(screen.getByText('Disable telemetry guide')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('查询'), { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: '检索' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('请输入查询。');
    expect(screen.queryByRole('heading', { name: '答案' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '查询追踪' })).not.toBeInTheDocument();
    expect(screen.queryByText('Disable telemetry guide')).not.toBeInTheDocument();
    expect(screen.queryByText('Telemetry can be disabled from the device settings privacy panel.')).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('clears stale answer state when submitting a blank Top K after a successful answer', async () => {
    const response: AnswerResponse = {
      query: { ...queryRecord, mode: 'answer' },
      answer: 'Telemetry can be disabled from the device settings privacy panel.',
      supporting_evidence: [evidence],
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(response));
    vi.stubGlobal('fetch', fetchMock);

    render(<SearchPage initialQuery="telemetry disable" />);
    fireEvent.click(screen.getByRole('button', { name: '生成答案' }));

    expect(await screen.findByRole('heading', { name: '答案' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '查询追踪' })).toBeInTheDocument();
    expect(screen.getByText('Disable telemetry guide')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('结果数'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: '生成答案' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('结果数必须在 1 到 50 之间。');
    expect(screen.queryByRole('heading', { name: '答案' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '查询追踪' })).not.toBeInTheDocument();
    expect(screen.queryByText('Disable telemetry guide')).not.toBeInTheDocument();
    expect(screen.queryByText('Telemetry can be disabled from the device settings privacy panel.')).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
