import { FormEvent, useEffect, useMemo, useState } from 'react';

import { getDatabaseOverview, listDatabaseRows, listDatabaseTables } from '../api/client';
import type { DatabaseJsonValue, DatabaseOverview, DatabaseTableMeta, DatabaseTableRow, DatabaseTableRowsPage } from '../api/types';
import { formatErrorMessage } from '../utils/errors';

const PAGE_SIZE = 50;
const LOADING_COPY = '正在加载数据库展示数据...';
const EMPTY_COPY = '数据库展示暂不可用。';

const COMMAND_GROUPS = [
  {
    title: '检查 PostgreSQL 是否在线',
    language: 'bash',
    command: 'pg_isready -h 127.0.0.1 -p 54329 -d intelligence_rag',
  },
  {
    title: '进入 psql 交互模式',
    language: 'bash',
    command: 'psql -h 127.0.0.1 -p 54329 -U intelligence -d intelligence_rag',
  },
  {
    title: 'psql 内部常用命令',
    language: 'sql',
    command: '\\dt\n\\d content_items\n\\x auto\n\\q',
  },
  {
    title: '查看核心表数量',
    language: 'sql',
    command:
      "select 'source_sites' as table_name, count(*) from source_sites\n" +
      "union all select 'crawl_runs', count(*) from crawl_runs\n" +
      "union all select 'raw_pages', count(*) from raw_pages\n" +
      "union all select 'content_items', count(*) from content_items\n" +
      "union all select 'content_chunks', count(*) from content_chunks\n" +
      "union all select 'search_queries', count(*) from search_queries;",
  },
  {
    title: '查看 chunk 与 Qdrant 的关联状态',
    language: 'sql',
    command:
      'select embed_status, vector_backend, count(*) as chunk_count\n' +
      'from content_chunks\n' +
      'group by 1, 2\n' +
      'order by 1, 2;',
  },
  {
    title: '查看 Qdrant 健康和 collection',
    language: 'bash',
    command:
      'curl -fsS http://127.0.0.1:6333/healthz\n' +
      'curl -fsS http://127.0.0.1:6333/collections/content_chunks_v1 | python3 -m json.tool',
  },
];

export function DatabasePage() {
  const [overview, setOverview] = useState<DatabaseOverview | null>(null);
  const [tables, setTables] = useState<DatabaseTableMeta[]>([]);
  const [selectedTable, setSelectedTable] = useState('');
  const [rowsPage, setRowsPage] = useState<DatabaseTableRowsPage | null>(null);
  const [selectedRow, setSelectedRow] = useState<DatabaseTableRow | null>(null);
  const [query, setQuery] = useState('');
  const [appliedQuery, setAppliedQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [rowsLoading, setRowsLoading] = useState(false);
  const [error, setError] = useState('');
  const [rowsError, setRowsError] = useState('');
  const [copyMessage, setCopyMessage] = useState('');

  useEffect(() => {
    let ignore = false;
    setLoading(true);
    setError('');
    Promise.all([getDatabaseOverview(), listDatabaseTables()])
      .then(([overviewResult, tablesResult]) => {
        if (ignore) {
          return;
        }
        setOverview(overviewResult);
        setTables(tablesResult.items);
        setSelectedTable((current) => current || tablesResult.items[0]?.table_name || '');
        setLoading(false);
      })
      .catch((reason: unknown) => {
        if (ignore) {
          return;
        }
        setError(formatErrorMessage('无法加载数据库展示数据', reason));
        setLoading(false);
      });
    return () => {
      ignore = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedTable) {
      setRowsPage(null);
      setSelectedRow(null);
      return;
    }
    let ignore = false;
    setRowsLoading(true);
    setRowsError('');
    setRowsPage(null);
    setSelectedRow(null);
    listDatabaseRows(selectedTable, { limit: PAGE_SIZE, offset, q: appliedQuery })
      .then((page) => {
        if (ignore) {
          return;
        }
        setRowsPage(page);
        setSelectedRow(page.items[0] || null);
        setRowsLoading(false);
      })
      .catch((reason: unknown) => {
        if (ignore) {
          return;
        }
        setRowsPage(null);
        setSelectedRow(null);
        setRowsError(formatErrorMessage('无法加载表数据', reason));
        setRowsLoading(false);
      });
    return () => {
      ignore = true;
    };
  }, [selectedTable, offset, appliedQuery]);

  const selectedTableMeta = useMemo(
    () => tables.find((table) => table.table_name === selectedTable) || null,
    [selectedTable, tables],
  );

  function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setOffset(0);
    setAppliedQuery(query.trim());
  }

  function handleTableChange(value: string) {
    setSelectedTable(value);
    setQuery('');
    setAppliedQuery('');
    setOffset(0);
  }

  async function copyCommand(title: string, command: string) {
    if (!navigator.clipboard) {
      setCopyMessage('当前浏览器不支持复制。');
      return;
    }
    try {
      await navigator.clipboard.writeText(command);
      setCopyMessage(`已复制：${title}`);
    } catch {
      setCopyMessage('复制失败，请手动复制。');
    }
  }

  const canGoPrevious = offset > 0;
  const canGoNext = rowsPage ? offset + rowsPage.limit < rowsPage.total : false;

  return (
    <section>
      <div className="page-title">
        <p className="eyebrow">只读数据库视图</p>
        <h1>数据库</h1>
        <p className="muted">只读展示，不支持 SQL 执行或写入操作。</p>
      </div>

      {error ? (
        <p className="card error-text" role="alert">
          {error}
        </p>
      ) : null}
      {loading ? (
        <p className="card status-line" role="status" aria-live="polite">
          {LOADING_COPY}
        </p>
      ) : null}
      {!loading && error ? <p className="card empty-state">{EMPTY_COPY}</p> : null}

      {overview ? <OverviewCards overview={overview} /> : null}

      <div className="grid database-layout">
        <section className="card database-browser-card">
          <div className="card-header">
            <div>
              <h2>核心表只读浏览器</h2>
              <p className="muted compact">白名单表、分页、关键词搜索；不提供任意 SQL。</p>
            </div>
            <span className="badge">read-only</span>
          </div>

          <div className="database-controls">
            <label htmlFor="database-table-select">选择表</label>
            <select
              id="database-table-select"
              value={selectedTable}
              onChange={(event) => handleTableChange(event.target.value)}
            >
              {tables.map((table) => (
                <option key={table.table_name} value={table.table_name}>
                  {table.label}
                </option>
              ))}
            </select>
          </div>

          {selectedTableMeta ? (
            <p className="muted compact">
              {selectedTableMeta.description} 默认排序：{selectedTableMeta.default_sort}
            </p>
          ) : null}

          <form className="inline-form" onSubmit={handleSearch}>
            <label className="sr-only" htmlFor="database-table-query">
              表内搜索
            </label>
            <input
              id="database-table-query"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="按白名单字段搜索"
            />
            <button type="submit">搜索表数据</button>
          </form>

          {rowsError ? <p className="error-text" role="alert">{rowsError}</p> : null}
          {rowsLoading ? <p className="status-line">正在加载表数据...</p> : null}
          {rowsPage ? (
            <>
              <p className="muted compact">
                total={rowsPage.total} limit={rowsPage.limit} offset={rowsPage.offset}
              </p>
              <DatabaseRowsTable rows={rowsPage.items} selectedRow={selectedRow} onSelectRow={setSelectedRow} />
              <div className="button-row">
                <button type="button" disabled={!canGoPrevious} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                  上一页
                </button>
                <button type="button" disabled={!canGoNext} onClick={() => setOffset(offset + PAGE_SIZE)}>
                  下一页
                </button>
              </div>
            </>
          ) : null}
        </section>

        <aside className="card row-detail-card">
          <h2>行详情</h2>
          {selectedRow ? <RowDetail row={selectedRow} /> : <p className="muted">选择一行查看详情。</p>}
        </aside>
      </div>

      <section className="card command-handbook" aria-label="psql 使用手册">
        <div className="card-header">
          <div>
            <h2>psql / Qdrant 命令手册</h2>
            <p className="muted compact">用于终端验证数据库和向量库接入；前端不会展示数据库密码。</p>
          </div>
          {copyMessage ? <span className="badge">{copyMessage}</span> : null}
        </div>
        <div className="grid command-grid">
          {COMMAND_GROUPS.map((group) => (
            <article className="command-card" key={group.title}>
              <div className="card-header compact-header">
                <h3>{group.title}</h3>
                <button type="button" onClick={() => copyCommand(group.title, group.command)}>
                  复制
                </button>
              </div>
              <p className="muted compact">{group.language}</p>
              <pre>{group.command}</pre>
            </article>
          ))}
        </div>
      </section>
    </section>
  );
}

function OverviewCards({ overview }: { overview: DatabaseOverview }) {
  const tableCounts = overview.postgres.table_counts;
  return (
    <>
      <div className="grid metric-grid">
        <div className="card metric-card">
          <span className="muted">PostgreSQL</span>
          <strong>{overview.postgres.status}</strong>
          <span>{overview.postgres.database_name}</span>
        </div>
        <div className="card metric-card">
          <span className="muted">Qdrant</span>
          <strong>{overview.qdrant.status}</strong>
          <span>{overview.qdrant.collection}</span>
        </div>
        <div className="card metric-card">
          <span className="muted">对账状态</span>
          <strong>{overview.reconciliation.status}</strong>
          <span>
            PostgreSQL {overview.reconciliation.postgres_qdrant_chunk_count} / Qdrant{' '}
            {overview.reconciliation.qdrant_points_count ?? '—'}
          </span>
        </div>
        <div className="card metric-card">
          <span className="muted">向量配置</span>
          <strong>{overview.qdrant.vector_size ?? '—'}</strong>
          <span>{overview.qdrant.distance ?? overview.qdrant.error ?? 'unknown'}</span>
        </div>
      </div>

      <div className="grid two-column">
        <section className="card">
          <h2>核心表计数</h2>
          <div className="grid counter-grid">
            {tableCounts.map((item) => (
              <div className="counter" key={item.table_name}>
                <span className="muted">{item.table_name}</span>
                <strong>{item.count}</strong>
              </div>
            ))}
          </div>
        </section>
        <section className="card">
          <h2>chunk 向量化状态</h2>
          {overview.postgres.chunk_vector_status.length ? (
            <ul className="dense-list">
              {overview.postgres.chunk_vector_status.map((item) => (
                <li key={`${item.embed_status}-${item.vector_backend ?? 'null'}`}>
                  <span className="badge">{item.count}</span>
                  <span>{item.embed_status} / {item.vector_backend ?? 'NULL'}</span>
                  <span className="muted">content_chunks</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">暂无 chunk 状态。</p>
          )}
        </section>
      </div>
    </>
  );
}

function DatabaseRowsTable({
  rows,
  selectedRow,
  onSelectRow,
}: {
  rows: DatabaseTableRow[];
  selectedRow: DatabaseTableRow | null;
  onSelectRow: (row: DatabaseTableRow) => void;
}) {
  if (!rows.length) {
    return <p className="empty-state">当前表没有匹配行。</p>;
  }
  const columns = Object.keys(rows[0].preview);
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>操作</th>
            {columns.map((column) => <th key={column}>{column}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr className={selectedRow?.id === row.id ? 'selected-row' : ''} key={row.id}>
              <td>
                <button type="button" aria-label={`查看 ${row.id}`} onClick={() => onSelectRow(row)}>
                  查看详情
                </button>
              </td>
              {columns.map((column) => <td key={column}>{formatValue(row.preview[column])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RowDetail({ row }: { row: DatabaseTableRow }) {
  return (
    <div>
      <p className="muted compact">{row.table_name}</p>
      <h3>ID: {row.id}</h3>
      <DetailSection title="Preview" value={row.preview} />
      <DetailSection title="Detail" value={row.detail} />
      <DetailSection title="Related" value={row.related} />
    </div>
  );
}

function DetailSection({ title, value }: { title: string; value: Record<string, DatabaseJsonValue> }) {
  if (!Object.keys(value).length) {
    return null;
  }
  const highlights = collectDetailHighlights(value);
  return (
    <section className="detail-section">
      <h4>{title}</h4>
      {highlights.length ? (
        <div className="detail-highlights">
          {highlights.map((highlight, index) => (
            <p className="muted compact" key={`${highlight}-${index}`}>
              {highlight}
            </p>
          ))}
        </div>
      ) : null}
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </section>
  );
}

function collectDetailHighlights(value: Record<string, DatabaseJsonValue>): string[] {
  const highlights = Object.entries(value).flatMap(([key, item]) => {
    if (isScalarDisplayValue(item)) {
      return key.endsWith('_preview') ? [String(item)] : [];
    }
    return collectScalarStrings(item);
  });
  return Array.from(new Set(highlights));
}

function collectScalarStrings(value: unknown): string[] {
  if (value === null || value === undefined) {
    return [];
  }
  if (isScalarDisplayValue(value)) {
    return [String(value)];
  }
  if (Array.isArray(value)) {
    return value.flatMap((item) => collectScalarStrings(item));
  }
  if (typeof value === 'object') {
    return Object.values(value).flatMap((item) => collectScalarStrings(item));
  }
  return [];
}

function isScalarDisplayValue(value: unknown): value is string | number | boolean {
  return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean';
}

function formatValue(value: DatabaseJsonValue): string {
  if (value === null || value === undefined) {
    return '—';
  }
  if (typeof value === 'object') {
    return JSON.stringify(value);
  }
  return String(value);
}
