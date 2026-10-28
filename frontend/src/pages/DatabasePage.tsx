import { FormEvent, useEffect, useMemo, useState } from 'react';

import { getDatabaseOverview, listDatabaseRows, listDatabaseTables, listDatabaseTargets } from '../api/client';
import type { DatabaseOverview, DatabaseTableMeta, DatabaseTableRowsPage, DatabaseTarget } from '../api/types';
import { formatErrorMessage } from '../utils/errors';

const PAGE_SIZE = 50;
const LOADING_COPY = '正在加载数据库展示数据...';
const EMPTY_COPY = '数据库展示暂不可用。';

export function DatabasePage() {
  const [overview, setOverview] = useState<DatabaseOverview | null>(null);
  const [tables, setTables] = useState<DatabaseTableMeta[]>([]);
  const [selectedTable, setSelectedTable] = useState('');
  const [rowsPage, setRowsPage] = useState<DatabaseTableRowsPage | null>(null);

  const [query, setQuery] = useState('');
  const [appliedQuery, setAppliedQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [rowsLoading, setRowsLoading] = useState(false);
  const [error, setError] = useState('');
  const [rowsError, setRowsError] = useState('');
  const [database, setDatabase] = useState('primary');
  const [targets, setTargets] = useState<DatabaseTarget[]>([]);
  const [catalogError, setCatalogError] = useState('');
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let ignore = false;
    listDatabaseTargets().then(result => { if (!ignore) setTargets(result.items); })
      .catch(reason => { if (!ignore) setCatalogError(formatErrorMessage('无法加载数据库列表', reason)); });
    return () => { ignore = true; };
  }, []);

  useEffect(() => {
    let ignore = false;
    setLoading(true);
    setError('');
    setOverview(null);
    Promise.all([getDatabaseOverview(database), listDatabaseTables(database)])
      .then(([overviewResult, tablesResult]) => {
        if (ignore) {
          return;
        }
        setOverview(overviewResult);
        setTables(tablesResult.items);
        setSelectedTable((current) => tablesResult.items.some(t => t.table_name === current) ? current : tablesResult.items[0]?.table_name || '');
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
  }, [database, revision]);

  useEffect(() => {
    if (!selectedTable) {
      setRowsPage(null);
      return;
    }
    let ignore = false;
    setRowsLoading(true);
    setRowsError('');
    setRowsPage(null);
    listDatabaseRows(selectedTable, { limit: PAGE_SIZE, offset, q: appliedQuery, database })
      .then((page) => {
        if (ignore) {
          return;
        }
        setRowsPage(page);
        setRowsLoading(false);
      })
      .catch((reason: unknown) => {
        if (ignore) {
          return;
        }
        setRowsPage(null);
        setRowsError(formatErrorMessage('无法加载表数据', reason));
        setRowsLoading(false);
      });
    return () => {
      ignore = true;
    };
  }, [selectedTable, offset, appliedQuery, database, revision]);

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

  const canGoPrevious = offset > 0;
  const canGoNext = rowsPage ? offset + rowsPage.limit < rowsPage.total : false;

  return (
    <section>
      <div className="page-title">
        <p className="eyebrow">只读数据库视图</p>
        <h1>数据库</h1>
        <p className="muted">只读展示，不支持 SQL 执行或写入操作。</p>
      </div>

      <div className="card database-controls">
        <label htmlFor="database-select">选择数据库</label>
        <select id="database-select" value={database} disabled={!targets.length} onChange={event => {
          setDatabase(event.target.value); setSelectedTable(''); setTables([]); setOverview(null);
          setRowsPage(null); setRowsError(''); setQuery(''); setAppliedQuery(''); setOffset(0);
        }}>
          {targets.length ? targets.map(target => <option key={target.id} value={target.id}>
            {target.label} · {target.database_name}
          </option>) : <option value="primary">业务数据库</option>}
        </select>
        <button type="button" disabled={loading || rowsLoading} onClick={() => setRevision(value => value + 1)}>刷新数据</button>
      </div>
      {catalogError ? <p className="error-text" role="alert">{catalogError}</p> : null}
      {database === 'documents' ? <p className="muted">文档库包含已发布与处理中版本。批处理期间计数持续变化；向量对账包含保留的历史版本。</p> : null}

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
              <DatabaseRowsTable rows={rowsPage.items} />
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
          <strong>{overview.qdrant.status === 'not_applicable' ? '不适用' : overview.qdrant.status}</strong>
          <span>{overview.qdrant.collection}</span>
        </div>
        <div className="card metric-card">
          <span className="muted">向量配置</span>
          <strong>{overview.qdrant.vector_size ?? '—'}</strong>
          <span>{overview.qdrant.distance ?? overview.qdrant.error ?? 'unknown'}</span>
        </div>
        <div className="card metric-card">
          <span className="muted">对账状态</span>
          <strong>{overview.reconciliation.status === 'not_applicable' ? '不适用' : overview.reconciliation.status}</strong>
          <span>
            PostgreSQL {overview.reconciliation.postgres_qdrant_chunk_count} / Qdrant{' '}
            {overview.reconciliation.qdrant_points_count ?? '—'}
          </span>
        </div>
      </div>

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
    </>
  );
}

function DatabaseRowsTable({ rows }: { rows: DatabaseTableRowsPage['items'] }) {
  if (!rows.length) {
    return <p className="empty-state">当前表没有匹配行。</p>;
  }
  const columns = Object.keys(rows[0].preview);
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              {columns.map((column) => <td key={column}>{formatValue(row.preview[column])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) {
    return '—';
  }
  if (typeof value === 'object') {
    return JSON.stringify(value);
  }
  return String(value);
}
