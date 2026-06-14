export type UUID = string;
export type ISODateTime = string;
export type JsonObject = Record<string, unknown>;

export type Page<T> = {
  items: T[];
  total: number;
  limit: number;
  offset: number;
};

export type SourceSite = {
  id: UUID;
  name: string;
  site_type: string;
  base_url: string;
  allowed_domains: string[];
  fetch_mode: string;
  default_language: string | null;
  active: boolean;
  config_json: JsonObject;
  created_at: ISODateTime;
  updated_at: ISODateTime;
};

export type CrawlJob = {
  id: UUID;
  source_site_id: UUID;
  name: string;
  trigger_mode: string;
  cron_expr: string | null;
  seed_config_json: JsonObject;
  parser_profile: string;
  max_pages: number;
  enabled: boolean;
  agent_policy_json: JsonObject;
  last_run_at: ISODateTime | null;
  next_run_at: ISODateTime | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
};

export type CrawlRun = {
  id: UUID;
  source_site_id: UUID;
  crawl_job_id: UUID;
  trigger_type: string;
  execution_mode: string;
  seed_url: string | null;
  status: string;
  started_at: ISODateTime | null;
  finished_at: ISODateTime | null;
  discovered_count: number;
  fetched_count: number;
  parsed_count: number;
  extracted_count: number;
  deduped_count: number;
  chunked_count: number;
  embedded_count: number;
  error_count: number;
  config_snapshot_json: JsonObject;
  error_message: string | null;
  created_at: ISODateTime;
};

export type CrawlRunEvent = {
  id: UUID;
  crawl_run_id: UUID;
  stage: string;
  level: string;
  event_type: string;
  message: string;
  related_url: string | null;
  related_raw_page_id: UUID | null;
  related_content_item_id: UUID | null;
  counters_json: JsonObject;
  agent_trace_json: JsonObject;
  created_at: ISODateTime;
};

export type ContentListItem = {
  id: UUID;
  source_site_id: UUID;
  item_type: string;
  canonical_url: string;
  title: string | null;
  author_name: string | null;
  published_at: ISODateTime | null;
  fetched_at: ISODateTime;
  summary_text: string | null;
  tags: string[];
  extraction_confidence: number | null;
};

export type RawPage = {
  id: UUID;
  source_site_id: UUID;
  crawl_run_id: UUID;
  requested_url: string;
  final_url: string | null;
  http_status: number | null;
  content_type: string | null;
  response_headers_json: JsonObject;
  raw_html: string | null;
  raw_text: string | null;
  raw_json: JsonObject;
  fetched_at: ISODateTime;
  fetch_error: string | null;
  parser_profile: string | null;
  extraction_method: string | null;
  extraction_confidence: number | null;
  parse_status: string;
  parse_error: string | null;
  body_hash: string | null;
  created_at: ISODateTime;
};

export type ContentChunk = {
  id: UUID;
  content_item_id: UUID;
  chunk_index: number;
  char_start: number | null;
  char_end: number | null;
  display_text: string;
  embed_text: string;
  token_count: number | null;
  chunk_metadata_json: JsonObject;
  qdrant_point_id: string | null;
  vector_backend: string | null;
  vector_point_id: string | null;
  embedded_at: ISODateTime | null;
  embed_status: string;
  embed_error: string | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
};

export type ContentDetail = ContentListItem & {
  raw_page: RawPage;
  source: SourceSite;
  crawl_run: CrawlRun;
  chunks: ContentChunk[];
};

export type SearchFilters = {
  source_site_id?: UUID;
  item_type?: string;
  language?: string;
  tags?: string[];
  published_after?: ISODateTime;
  published_before?: ISODateTime;
};

export type SearchRequest = {
  query: string;
  mode?: 'search' | 'answer';
  filters?: SearchFilters;
  top_k?: number;
};

export type SearchQueryRead = {
  id: UUID;
  raw_query: string;
  filter_json: JsonObject;
  mode: string;
  top_k: number;
  used_agent: boolean;
  optimized_query_text: string | null;
  keyword_terms_json: string[];
  entity_hints_json: string[];
  time_hints_json: JsonObject;
  result_summary_json: JsonObject;
  query_trace_json: JsonObject;
  result_count: number | null;
  created_at: ISODateTime;
};

export type EvidenceObject = {
  chunk_id: UUID;
  content_item_id: UUID;
  raw_page_id: UUID;
  source_site_id: UUID;
  title: string | null;
  snippet: string;
  canonical_url: string;
  source_site_name: string;
  author_name: string | null;
  published_at: ISODateTime | null;
  item_type: string;
  score: number;
  vector_score: number | null;
  keyword_score: number | null;
  matched_by: 'vector' | 'keyword' | 'hybrid';
  thread_summary: string | null;
};

export type SearchResponse = {
  query: SearchQueryRead;
  evidence: EvidenceObject[];
};

export type AnswerResponse = {
  query: SearchQueryRead;
  answer: string;
  supporting_evidence: EvidenceObject[];
};
