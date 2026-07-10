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
  video_url: string | null;
  description_text: string | null;
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

export type DatabaseTableCount = {
  table_name: string;
  count: number;
};

export type ChunkVectorStatus = {
  embed_status: string;
  vector_backend: string | null;
  count: number;
};

export type DatabaseRunSummary = {
  id: UUID;
  status: string;
  created_at: ISODateTime;
  parsed_count: number;
  chunked_count: number;
  embedded_count: number;
  error_count: number;
};

export type DatabaseSearchSummary = {
  id: UUID;
  raw_query: string;
  mode: string;
  result_count: number | null;
  created_at: ISODateTime;
};

export type PostgresOverview = {
  status: 'ok';
  database_name: string;
  server_version: string;
  checked_at: ISODateTime;
  table_counts: DatabaseTableCount[];
  latest_run: DatabaseRunSummary | null;
  latest_search: DatabaseSearchSummary | null;
  chunk_vector_status: ChunkVectorStatus[];
};

export type QdrantOverview = {
  status: 'ok' | 'unavailable';
  collection: string;
  points_count: number | null;
  vector_size: number | null;
  distance: string | null;
  error: string | null;
};

export type DatabaseReconciliation = {
  postgres_qdrant_chunk_count: number;
  qdrant_points_count: number | null;
  status: 'matched' | 'mismatch' | 'unknown';
};

export type DatabaseOverview = {
  postgres: PostgresOverview;
  qdrant: QdrantOverview;
  reconciliation: DatabaseReconciliation;
};

export type DatabaseTableMeta = {
  table_name: string;
  label: string;
  description: string;
  default_sort: string;
  preview_columns: string[];
  searchable_columns: string[];
};

export type DatabaseTableList = {
  items: DatabaseTableMeta[];
  total: number;
};

export type DatabaseJsonValue = JsonObject | unknown[] | string | number | boolean | null;

export type DatabaseTableRow = {
  id: UUID;
  table_name: string;
  preview: Record<string, DatabaseJsonValue>;
  detail: Record<string, DatabaseJsonValue>;
  related: Record<string, DatabaseJsonValue>;
};

export type DatabaseTableRowsPage = {
  items: DatabaseTableRow[];
  total: number;
  limit: number;
  offset: number;
};

export type LiteratureRunCreate = {
  query: string;
  year_from: number;
  direction_count: number;
  candidates_per_direction: number;
  top_n_per_direction: number;
  include_fulltext: boolean;
};

export type LiteratureRun = {
  id: UUID;
  query: string;
  status: string;
  current_stage: string;
  progress_current: number;
  progress_total: number;
  progress_message: string | null;
  options_json: JsonObject;
  directions_json: JsonObject[];
  raw_search_results_json: JsonObject;
  picks_json: JsonObject[];
  analyses_json: JsonObject[];
  final_report_markdown: string | null;
  error_message: string | null;
  started_at: ISODateTime | null;
  finished_at: ISODateTime | null;
  created_at: ISODateTime;
  updated_at: ISODateTime;
};

export type LiteratureRunEvent = {
  id: UUID;
  literature_run_id: UUID;
  stage: string;
  level: string;
  event_type: string;
  message: string;
  counters_json: JsonObject;
  trace_json: JsonObject;
  created_at: ISODateTime;
};

export type LiteratureArtifact = {
  id: UUID;
  literature_run_id: UUID;
  paper_id: UUID | null;
  artifact_type: string;
  mime_type: string | null;
  byte_size: number | null;
  sha256: string | null;
  source_url: string | null;
  created_at: ISODateTime;
};

export type LiteraturePaper = {
  id: UUID;
  ieee_article_number: string | null;
  doi: string | null;
  title: string;
  authors_json: JsonObject[];
  abstract: string | null;
  publication_title: string | null;
  publication_year: number | null;
  citation_count: number;
  access_type: string | null;
  document_url: string | null;
  pdf_url: string | null;
  raw_metadata_json: JsonObject;
  created_at: ISODateTime;
  updated_at: ISODateTime;
};

export type LiteratureEvidence = {
  id: UUID;
  literature_run_paper_id: UUID;
  matched_term: string;
  match_level: string;
  evidence_text: string;
  evidence_source: string;
  page_number: number | null;
  section_name: string | null;
  char_start: number | null;
  char_end: number | null;
  verified: boolean;
  created_at: ISODateTime;
};

export type LiteratureRunPaper = {
  id: UUID;
  literature_run_id: UUID;
  paper_id: UUID;
  direction_id: string;
  direction_title: string | null;
  search_query: string;
  candidate_rank: number | null;
  selected_rank: number | null;
  score: number | null;
  score_detail_json: JsonObject;
  selected: boolean;
  analysis_status: string;
  abstract_zh: string | null;
  match_how: string | null;
  match_use: string | null;
  conclusion: string | null;
  analysis_json: JsonObject;
  created_at: ISODateTime;
  updated_at: ISODateTime;
};

export type LiteratureResultItem = {
  selection: LiteratureRunPaper;
  paper: LiteraturePaper;
  evidence: LiteratureEvidence[];
  artifacts: LiteratureArtifact[];
};

export type LiteratureRunResults = {
  run: LiteratureRun;
  items: LiteratureResultItem[];
  run_artifacts: LiteratureArtifact[];
};
