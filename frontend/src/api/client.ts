import type {
  AnswerRequest,
  AnswerResponse,
  AnswerStreamEvent,
  ChatSession,
  ChatSessionDetail,
  ContentDetail,
  ContentListItem,
  CrawlJob,
  DatabaseOverview,
  DatabaseTableList,
  DatabaseTableRow,
  DatabaseTableRowsPage,
  DroneCatalog,
  DroneDetail,
  CrawlRun,
  CrawlRunEvent,
  LiteratureRun,
  LiteratureRunCreate,
  LiteratureRunEvent,
  LiteratureRunResults,
  Page,
  SearchRequest,
  SearchResponse,
  SourceSite,
  SupplierOverview,
  SupplierRelation,
  SupplierVerification,
  UUID,
  VideoGraphData,
  VideoGraphEvidence,
  VideoGraphOverview,
  VideoGraphStatus,
} from './types';

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

export function listDrones(signal?: AbortSignal): Promise<DroneCatalog> {
  return request('/drones', { signal });
}

export function getDrone(slug: string, signal?: AbortSignal): Promise<DroneDetail> {
  return request(`/drones/${encodeURIComponent(slug)}`, { signal });
}

export function getVideoGraphOverview(): Promise<VideoGraphOverview> {
  return request('/video-graph/overview');
}

export type VideoGraphParams = {
  q?: string; entity_id?: UUID; content_item_id?: UUID; entity_type?: string;
  assertion?: string; hops?: number; max_nodes?: number; max_edges?: number; full?: boolean;
};

export function getVideoGraph(params: VideoGraphParams = {}): Promise<VideoGraphData> {
  return request(buildPath('/video-graph/graph', params));
}

export function getVideoGraphEvidence(
  relationId: UUID, params: { content_item_id?: UUID; assertion?: string; limit?: number; offset?: number } = {},
): Promise<Page<VideoGraphEvidence>> {
  return request(buildPath(`/video-graph/relations/${relationId}/evidence`, params));
}

export function getVideoGraphStatus(contentId: UUID): Promise<VideoGraphStatus> {
  return request(`/video-graph/videos/${contentId}/status`);
}

export type ScheduledTask = {
  id: string; name: string; timer: string; scope: string; schedule: string;
  timer_state: string; enabled: string; next_run: string; last_run: string;
  service_state: string; last_result: string; exit_code: string; available: boolean;
};

export function listScheduledTasks(): Promise<{ items: ScheduledTask[] }> {
  return request('/scheduled-tasks');
}

export function triggerScheduledTask(id: string, token: string): Promise<{ status: string }> {
  return request(`/scheduled-tasks/${encodeURIComponent(id)}/trigger`, {
    method: 'POST', headers: { Authorization: `Bearer ${token}` },
  });
}

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
  if (response.status === 204) return undefined as T;
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

export function createLiteratureRun(requestBody: LiteratureRunCreate): Promise<LiteratureRun> {
  return jsonPost<LiteratureRun>('/literature-runs', requestBody);
}

export function listLiteratureRuns(limit = 20, offset = 0): Promise<Page<LiteratureRun>> {
  return request<Page<LiteratureRun>>(buildPath('/literature-runs', { limit, offset }));
}

export function getLiteratureRun(id: UUID): Promise<LiteratureRun> {
  return request<LiteratureRun>(`/literature-runs/${id}`);
}

export function getLiteratureRunEvents(id: UUID, limit = 100, offset = 0): Promise<Page<LiteratureRunEvent>> {
  return request<Page<LiteratureRunEvent>>(buildPath(`/literature-runs/${id}/events`, { limit, offset }));
}

export function getLiteratureRunResults(id: UUID): Promise<LiteratureRunResults> {
  return request<LiteratureRunResults>(`/literature-runs/${id}/results`);
}

export function cancelLiteratureRun(id: UUID): Promise<LiteratureRun> {
  return jsonPost<LiteratureRun>(`/literature-runs/${id}/cancel`, {});
}

export function answer(requestBody: AnswerRequest): Promise<AnswerResponse> {
  return jsonPost<AnswerResponse>('/answer', {
    ...requestBody,
    mode: 'answer',
    filters: requestBody.filters ?? {},
    top_k: requestBody.top_k ?? 10,
  });
}
export function getLiteratureArtifactUrl(id: UUID): string {
  const base = API_BASE_URL.replace(/\/+$/, '');
  return `${base}/literature-artifacts/${id}/download`;
}

export function listChatSessions(signal?: AbortSignal): Promise<ChatSession[]> {
  return request('/chat-sessions', { signal });
}

export function createChatSession(signal?: AbortSignal): Promise<ChatSession> {
  return request('/chat-sessions', { method: 'POST', signal });
}

export function getChatSession(id: UUID, signal?: AbortSignal): Promise<ChatSessionDetail> {
  return request(`/chat-sessions/${encodeURIComponent(id)}`, { signal });
}

export function deleteChatSession(id: UUID, signal?: AbortSignal): Promise<void> {
  return request(`/chat-sessions/${encodeURIComponent(id)}`, { method: 'DELETE', signal });
}

export async function streamAnswer(
  body: AnswerRequest,
  onEvent: (event: AnswerStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE_URL.replace(/\/+$/, '')}/answer/stream`, {
    method: 'POST', signal,
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ ...body, mode: 'answer' }),
  });
  if (!response.ok) throw new Error(await response.text());
  if (!response.body) throw new Error('浏览器未提供流式响应');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let completed = false;
  try {
    while (!completed) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let boundary: RegExpMatchArray | null;
      while ((boundary = buffer.match(/\r?\n\r?\n/))) {
        const frame = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index! + boundary[0].length);
        const lines = frame.split(/\r?\n/);
        const event = lines.find((line) => line.startsWith('event:'))?.slice(6).trim();
        const data = lines.filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart()).join('\n');
        if (!event || !data) continue;
        const parsed = JSON.parse(data);
        if (event === 'error') throw new Error(parsed.message || '问答处理失败');
        if (['status', 'evidence', 'delta', 'reset', 'done'].includes(event)) {
          onEvent({ event, data: parsed } as AnswerStreamEvent);
        }
        if (event === 'done') { completed = true; break; }
      }
      if (done) break;
    }
    if (!completed) throw new Error('回答连接中断，内容可能不完整，请重试');
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export function getSupplierOverview(): Promise<SupplierOverview> {
  return request<SupplierOverview>('/supplier/overview');
}

export function listSupplierRelations(
  params: { q?: string; credibility?: string; verify_status?: string; limit?: number; offset?: number } = {},
): Promise<Page<SupplierRelation>> {
  return request<Page<SupplierRelation>>(
    buildPath('/supplier/relations', {
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
      q: params.q,
      credibility: params.credibility,
      verify_status: params.verify_status,
    }),
  );
}

export function listSupplierVerifications(relationId: UUID): Promise<SupplierVerification[]> {
  return request<SupplierVerification[]>(`/supplier/relations/${relationId}/verifications`);
}
