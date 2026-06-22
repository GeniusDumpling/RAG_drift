import type {
  AnswerResponse,
  ContentDetail,
  ContentListItem,
  CrawlJob,
  DatabaseOverview,
  DatabaseTableList,
  DatabaseTableRow,
  DatabaseTableRowsPage,
  CrawlRun,
  CrawlRunEvent,
  Page,
  SearchRequest,
  SearchResponse,
  SourceSite,
  UUID,
} from './types';

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

function buildPath(path: string, params: Record<string, unknown> = {}): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') {
      continue;
    }
    if (Array.isArray(value)) {
      for (const item of value) {
        query.append(key, String(item));
      }
    } else {
      query.set(key, String(value));
    }
  }
  const suffix = query.toString();
  return suffix ? `${path}?${suffix}` : path;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const base = API_BASE_URL.replace(/\/+$/, '');
  const response = await fetch(`${base}${path}`, init);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return (await response.json()) as T;
}

function jsonPost<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function listSources(limit = 50, offset = 0): Promise<Page<SourceSite>> {
  return request<Page<SourceSite>>(buildPath('/sources', { limit, offset }));
}

export function listJobs(limit = 50, offset = 0): Promise<Page<CrawlJob>> {
  return request<Page<CrawlJob>>(buildPath('/jobs', { limit, offset }));
}

export function listRuns(limit = 50, offset = 0): Promise<Page<CrawlRun>> {
  return request<Page<CrawlRun>>(buildPath('/runs', { limit, offset }));
}

export function getRun(id: UUID): Promise<CrawlRun> {
  return request<CrawlRun>(`/runs/${id}`);
}

export function getRunEvents(id: UUID, limit = 100, offset = 0): Promise<Page<CrawlRunEvent>> {
  return request<Page<CrawlRunEvent>>(buildPath(`/runs/${id}/events`, { limit, offset }));
}

export function triggerJob(jobId: UUID, seedUrl?: string): Promise<CrawlRun> {
  return jsonPost<CrawlRun>(`/jobs/${jobId}/trigger`, { seed_url: seedUrl || null });
}

export function listContents(
  params: { source_site_id?: UUID; item_type?: string; q?: string; limit?: number; offset?: number } = {},
): Promise<Page<ContentListItem>> {
  return request<Page<ContentListItem>>(
    buildPath('/contents', {
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
      source_site_id: params.source_site_id,
      item_type: params.item_type,
      q: params.q,
    }),
  );
}

export function getContent(id: UUID): Promise<ContentDetail> {
  return request<ContentDetail>(`/contents/${id}`);
}

export function getDatabaseOverview(): Promise<DatabaseOverview> {
  return request<DatabaseOverview>('/database/overview');
}

export function listDatabaseTables(): Promise<DatabaseTableList> {
  return request<DatabaseTableList>('/database/tables');
}

export function listDatabaseRows(
  tableName: string,
  params: { q?: string; limit?: number; offset?: number } = {},
): Promise<DatabaseTableRowsPage> {
  return request<DatabaseTableRowsPage>(
    buildPath(`/database/tables/${tableName}/rows`, {
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
      q: params.q,
    }),
  );
}

export function getDatabaseRow(tableName: string, rowId: UUID): Promise<DatabaseTableRow> {
  return request<DatabaseTableRow>(`/database/tables/${tableName}/rows/${rowId}`);
}

export function search(requestBody: SearchRequest): Promise<SearchResponse> {
  return jsonPost<SearchResponse>('/search', {
    ...requestBody,
    mode: 'search',
    filters: requestBody.filters ?? {},
    top_k: requestBody.top_k ?? 10,
  });
}

export function answer(requestBody: SearchRequest): Promise<AnswerResponse> {
  return jsonPost<AnswerResponse>('/answer', {
    ...requestBody,
    mode: 'answer',
    filters: requestBody.filters ?? {},
    top_k: requestBody.top_k ?? 10,
  });
}
