import { FormEvent, useEffect, useState } from 'react';

import {
  getSupplierOverview,
  listSupplierRelations,
  listSupplierVerifications,
} from '../api/client';
import type {
  Page,
  SupplierOverview,
  SupplierRelation,
  SupplierVerification,
} from '../api/types';
import { formatErrorMessage } from '../utils/errors';
import { safeExternalHref } from '../utils/links';

const PAGE_SIZE = 50;
const CREDIBILITY_OPTIONS = ['明确', '疑似'];
const VERIFY_STATUS_OPTIONS = ['未验证', '已验证', '验证失败'];

function fmtTime(iso: string | null | undefined): string {
  if (!iso) {
    return '—';
  }
  return iso.replace('T', ' ').slice(0, 19);
}

function UrlList({ urls, limit = 5 }: { urls: string[]; limit?: number }) {
  if (!urls.length) {
    return <span className="muted">（无）</span>;
  }
  const visible = urls.slice(0, limit);
  const rest = urls.length - visible.length;
  return (
    <ul className="url-list">
      {visible.map((url) => {
        const href = safeExternalHref(url);
        return href ? (
          <li key={url}>
            <a href={href} target="_blank" rel="noreferrer">
              {url}
            </a>
          </li>
        ) : (
          <li key={url}>{url}</li>
        );
      })}
      {rest > 0 ? <li className="muted">… 另 {rest} 条</li> : null}
    </ul>
  );
}

function OverviewCards({ overview }: { overview: SupplierOverview }) {
  const cards = [
    { label: '供应商总数', value: overview.total_suppliers },
    { label: '明确供应关系', value: overview.confirmed_relations },
    { label: '已验证', value: overview.verified },
    { label: '待验证', value: overview.unverified },
    { label: '验证失败', value: overview.verify_failed },
  ];
  return (
    <div className="grid metric-grid">
      {cards.map((card) => (
        <div className="card metric-card" key={card.label}>
          <span className="muted">{card.label}</span>
          <strong>{card.value}</strong>
        </div>
      ))}
    </div>
  );
}

type RelationsTableProps = {
  relations: Page<SupplierRelation>;
  selectedId: string | null;
  onSelect: (relation: SupplierRelation) => void;
};

function RelationsTable({ relations, selectedId, onSelect }: RelationsTableProps) {
  if (!relations.items.length) {
    return <p className="empty-state">没有匹配的供应关系。</p>;
  }
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>供应商</th>
            <th>供应内容 / 模块</th>
            <th>可信度</th>
            <th>证据来源</th>
            <th>验证状态</th>
            <th>更新时间</th>
          </tr>
        </thead>
        <tbody>
          {relations.items.map((relation) => (
            <tr
              aria-selected={relation.id === selectedId}
              className={relation.id === selectedId ? 'selected-row' : ''}
              key={relation.id}
            >
              <td>
                <button className="link-button" type="button" onClick={() => onSelect(relation)}>
                  {relation.supplier_name}
                </button>
              </td>
              <td>{relation.supply_content || '—'}</td>
              <td>
                <span className="badge">{relation.credibility}</span>
              </td>
              <td>{relation.source_urls.length}</td>
              <td>{relation.verify_status}</td>
              <td>{fmtTime(relation.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function VerificationDetail({
  relation,
  verifications,
  loading,
  error,
}: {
  relation: SupplierRelation;
  verifications: SupplierVerification[];
  loading: boolean;
  error: string;
}) {
  return (
    <section className="card" aria-label="供应商验证明细">
      <div className="card-header">
        <div>
          <h2>{relation.supplier_name}</h2>
          <p className="muted compact">
            供应：{relation.supply_content || '—'} · 状态：{relation.verify_status} · 出现{' '}
            {relation.seen_count} 次
          </p>
        </div>
        <span className="badge">{relation.credibility}</span>
      </div>

      {relation.buyer_name ? (
        <p className="muted compact">采购方：{relation.buyer_name}</p>
      ) : null}

      {relation.source_urls.length ? (
        <div className="detail-block">
          <h3>供应关系证据来源</h3>
          <UrlList urls={relation.source_urls} />
        </div>
      ) : null}

      <div className="detail-block">
        <h3>定向验证记录（{verifications.length}）</h3>
        {error ? <p className="error-text" role="alert">{error}</p> : null}
        {loading ? <p className="status-line">正在加载验证记录...</p> : null}
        {!loading && !error && verifications.length === 0 ? (
          <p className="empty-state">该供应商尚无验证记录。</p>
        ) : null}
        {!loading && !error
          ? verifications.map((verification) => (
              <article className="verification-item" key={verification.id}>
                <div className="verification-head">
                  <span className="badge">{verification.verdict}</span>
                  {verification.confidence ? (
                    <span className="muted">可信度：{verification.confidence}</span>
                  ) : null}
                  <span className="muted">{fmtTime(verification.verify_time ?? verification.created_at)}</span>
                </div>
                {verification.supply_content ? (
                  <p className="detail-block">验证后供应：{verification.supply_content}</p>
                ) : null}
                {verification.evidence_urls.length ? (
                  <div className="detail-block">
                    <p className="muted compact">证据 URL：</p>
                    <UrlList urls={verification.evidence_urls} />
                  </div>
                ) : null}
                {verification.evidence_md ? (
                  <details className="snippet-expand">
                    <summary>查看完整判定证据</summary>
                    <pre className="evidence-pre">{verification.evidence_md}</pre>
                  </details>
                ) : null}
              </article>
            ))
          : null}
      </div>
    </section>
  );
}

export function SupplierPage() {
  const [overview, setOverview] = useState<SupplierOverview | null>(null);
  const [relations, setRelations] = useState<Page<SupplierRelation> | null>(null);
  const [selected, setSelected] = useState<SupplierRelation | null>(null);
  const [verifications, setVerifications] = useState<SupplierVerification[]>([]);

  const [query, setQuery] = useState('');
  const [appliedQuery, setAppliedQuery] = useState('');
  const [credibility, setCredibility] = useState('');
  const [verifyStatus, setVerifyStatus] = useState('');
  const [offset, setOffset] = useState(0);

  const [overviewLoading, setOverviewLoading] = useState(true);
  const [listLoading, setListLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [listError, setListError] = useState('');
  const [detailError, setDetailError] = useState('');

  useEffect(() => {
    let ignore = false;
    getSupplierOverview()
      .then((result) => {
        if (!ignore) {
          setOverview(result);
        }
      })
      .catch(() => {
        if (!ignore) {
          setOverview(null);
        }
      })
      .finally(() => {
        if (!ignore) {
          setOverviewLoading(false);
        }
      });
    return () => {
      ignore = true;
    };
  }, []);

  useEffect(() => {
    let ignore = false;
    setListLoading(true);
    setListError('');
    setRelations(null);
    listSupplierRelations({
      limit: PAGE_SIZE,
      offset,
      q: appliedQuery || undefined,
      credibility: credibility || undefined,
      verify_status: verifyStatus || undefined,
    })
      .then((result) => {
        if (!ignore) {
          setRelations(result);
        }
      })
      .catch((reason: unknown) => {
        if (!ignore) {
          setRelations(null);
          setListError(formatErrorMessage('无法加载供应关系', reason));
        }
      })
      .finally(() => {
        if (!ignore) {
          setListLoading(false);
        }
      });
    return () => {
      ignore = true;
    };
  }, [appliedQuery, credibility, verifyStatus, offset]);

  useEffect(() => {
    if (!selected) {
      setVerifications([]);
      return;
    }
    let ignore = false;
    setDetailLoading(true);
    setDetailError('');
    setVerifications([]);
    listSupplierVerifications(selected.id)
      .then((result) => {
        if (!ignore) {
          setVerifications(result);
        }
      })
      .catch((reason: unknown) => {
        if (!ignore) {
          setDetailError(formatErrorMessage('无法加载验证记录', reason));
        }
      })
      .finally(() => {
        if (!ignore) {
          setDetailLoading(false);
        }
      });
    return () => {
      ignore = true;
    };
  }, [selected]);

  function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setOffset(0);
    setAppliedQuery(query.trim());
  }

  function handleFilterChange(reset: () => void) {
    setOffset(0);
    reset();
  }

  const canGoPrevious = offset > 0;
  const canGoNext = relations ? offset + relations.limit < relations.total : false;

  return (
    <section>
      <div className="page-title">
        <p className="eyebrow">供应链情报</p>
        <h1>供应链</h1>
        <p className="muted">大疆相关供应关系（阶段1 抽取）与定向验证（阶段2）只读展示。</p>
      </div>

      {overviewLoading ? <p className="card status-line">正在加载统计...</p> : null}
      {!overviewLoading && overview ? <OverviewCards overview={overview} /> : null}
      {!overviewLoading && !overview ? (
        <p className="card empty-state" role="alert">
          供应链统计暂不可用，请确认后端已启动且 supplier API 已启用。
        </p>
      ) : null}

      <section className="card">
        <div className="card-header">
          <div>
            <h2>供应关系</h2>
            <p className="muted compact">按供应商聚合；点击供应商名查看验证记录。</p>
          </div>
        </div>

        <div className="filter-row">
          <form className="inline-form" onSubmit={handleSearch}>
            <label className="sr-only" htmlFor="supplier-query">
              搜索供应商
            </label>
            <input
              id="supplier-query"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="按供应商或供应内容搜索"
            />
            <button type="submit">搜索</button>
          </form>
          <label htmlFor="supplier-credibility">
            可信度
            <select
              id="supplier-credibility"
              value={credibility}
              onChange={(event) => handleFilterChange(() => setCredibility(event.target.value))}
            >
              <option value="">全部</option>
              {CREDIBILITY_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <label htmlFor="supplier-verify-status">
            验证状态
            <select
              id="supplier-verify-status"
              value={verifyStatus}
              onChange={(event) => handleFilterChange(() => setVerifyStatus(event.target.value))}
            >
              <option value="">全部</option>
              {VERIFY_STATUS_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
        </div>

        {listError ? <p className="error-text" role="alert">{listError}</p> : null}
        {listLoading ? <p className="status-line">正在加载供应关系...</p> : null}
        {relations ? (
          <>
            <p className="muted compact">
              total={relations.total} limit={relations.limit} offset={relations.offset}
            </p>
            <RelationsTable relations={relations} selectedId={selected?.id ?? null} onSelect={setSelected} />
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

      {selected ? (
        <VerificationDetail
          relation={selected}
          verifications={verifications}
          loading={detailLoading}
          error={detailError}
        />
      ) : null}
    </section>
  );
}

export default SupplierPage;