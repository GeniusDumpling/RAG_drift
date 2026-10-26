CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS drone_models (
    id uuid PRIMARY KEY, slug text UNIQUE NOT NULL, name text NOT NULL,
    manufacturer text NOT NULL
);
CREATE TABLE IF NOT EXISTS file_assets (
    id uuid PRIMARY KEY, sha256 text UNIQUE NOT NULL, storage_key text NOT NULL,
    mime_type text NOT NULL, bytes bigint NOT NULL CHECK(bytes >= 0),
    width integer, height integer
);
CREATE TABLE IF NOT EXISTS document_versions (
    id uuid PRIMARY KEY, document_key text NOT NULL, title text NOT NULL,
    document_type text NOT NULL, official_version text, published_at date,
    source_url text NOT NULL, source_asset_id uuid NOT NULL REFERENCES file_assets(id),
    active_processing_id uuid, metadata_json jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS document_model_links (
    document_version_id uuid REFERENCES document_versions(id),
    model_id uuid REFERENCES drone_models(id), applicability_note text NOT NULL DEFAULT '',
    PRIMARY KEY(document_version_id, model_id)
);
CREATE TABLE IF NOT EXISTS document_processings (
    id uuid PRIMARY KEY, document_version_id uuid NOT NULL REFERENCES document_versions(id),
    parser_version text NOT NULL, config_hash text NOT NULL, language_policy text NOT NULL,
    chunker_version text NOT NULL, status text NOT NULL,
    artifact_root text NOT NULL, metadata_json jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(), indexed_at timestamptz,
    UNIQUE(document_version_id, config_hash)
);
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='document_active_processing_fk') THEN
        ALTER TABLE document_versions ADD CONSTRAINT document_active_processing_fk
            FOREIGN KEY(active_processing_id) REFERENCES document_processings(id);
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS document_pages (
    id uuid PRIMARY KEY, processing_id uuid NOT NULL REFERENCES document_processings(id),
    source_page_no integer NOT NULL CHECK(source_page_no > 0), printed_page_label text,
    width double precision NOT NULL, height double precision NOT NULL,
    rotation integer NOT NULL, language_state text NOT NULL,
    included boolean NOT NULL, inclusion_reason text NOT NULL,
    render_asset_id uuid REFERENCES file_assets(id), metadata_json jsonb NOT NULL DEFAULT '{}',
    UNIQUE(processing_id, source_page_no)
);
CREATE TABLE IF NOT EXISTS document_elements (
    id uuid PRIMARY KEY, processing_id uuid NOT NULL REFERENCES document_processings(id),
    element_type text NOT NULL, reading_order integer NOT NULL,
    section_path text NOT NULL, language text NOT NULL, text text NOT NULL,
    table_json jsonb, asset_id uuid REFERENCES file_assets(id),
    metadata_json jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS element_regions (
    element_id uuid REFERENCES document_elements(id), page_id uuid REFERENCES document_pages(id),
    region_index integer NOT NULL, bbox jsonb NOT NULL,
    PRIMARY KEY(element_id, page_id, region_index)
);
CREATE TABLE IF NOT EXISTS element_links (
    from_element_id uuid REFERENCES document_elements(id),
    to_element_id uuid REFERENCES document_elements(id), relation_type text NOT NULL,
    PRIMARY KEY(from_element_id, to_element_id, relation_type)
);
CREATE TABLE IF NOT EXISTS content_chunks (
    id uuid PRIMARY KEY, processing_id uuid NOT NULL REFERENCES document_processings(id),
    chunk_index integer NOT NULL, chunk_type text NOT NULL, section_path text NOT NULL,
    display_text text NOT NULL, embed_text text NOT NULL, content_hash text NOT NULL,
    index_language text NOT NULL, metadata_json jsonb NOT NULL DEFAULT '{}',
    embed_status text NOT NULL DEFAULT 'pending',
    UNIQUE(processing_id, chunk_index)
);
CREATE TABLE IF NOT EXISTS chunk_elements (
    chunk_id uuid REFERENCES content_chunks(id), element_id uuid REFERENCES document_elements(id),
    sequence_no integer NOT NULL, PRIMARY KEY(chunk_id, element_id)
);
CREATE TABLE IF NOT EXISTS vector_index_jobs (
    chunk_id uuid REFERENCES content_chunks(id), collection text NOT NULL,
    model_version text NOT NULL, input_hash text NOT NULL, status text NOT NULL,
    attempts integer NOT NULL DEFAULT 0, last_error text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(chunk_id, collection, model_version)
);
CREATE INDEX IF NOT EXISTS processing_document_idx ON document_processings(document_version_id);
CREATE INDEX IF NOT EXISTS elements_processing_order_idx ON document_elements(processing_id, reading_order);
CREATE INDEX IF NOT EXISTS chunks_processing_idx ON content_chunks(processing_id, chunk_index);
CREATE INDEX IF NOT EXISTS jobs_status_idx ON vector_index_jobs(status, updated_at);
CREATE INDEX IF NOT EXISTS regions_page_idx ON element_regions(page_id);
INSERT INTO schema_migrations(version) VALUES ('0001_drone_documents') ON CONFLICT DO NOTHING;
