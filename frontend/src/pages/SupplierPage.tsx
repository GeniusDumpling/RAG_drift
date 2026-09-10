import { FormEvent, Fragment, useEffect, useState } from 'react';

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
const EVIDENCE_URL_PATTERN = /(https?:\/\/[^\s)\]}]+)/g;

function verificationConfidenceClass(confidence: string | null): string {
  if (confidence === '明确') return 'verification-confidence-explicit';
  if (confidence === '疑似') return 'verification-confidence-suspected';
  if (confidence === '不相关') return 'verification-confidence-unclear';
  return 'verification-confidence-unknown';
}

function ExternalLink({ href, children }: { href: string; children: string }) {
  const safeHref = safeExternalHref(href);
  return safeHref ? (
    <a href={safeHref} rel="noreferrer" target="_blank">
      {children}
    </a>
  ) : (
    <span>{href}</span>
  );
}

function EvidenceText({ text }: { text: string }) {
  return (
    <pre className="evidence-pre">
      {text.split(EVIDENCE_URL_PATTERN).map((part, index) => {
        const href = safeExternalHref(part);
        return href ? (
          <a href={href} key={`${part}-${index}`} rel="noreferrer" target="_blank">
            {part}
          </a>
        ) : (
          part
        );
      })}
    </pre>
  );
}

function VerificationExpansion({
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
    <tr className="supplier-verification-row">
      <td colSpan={4}>
        <section aria-label={`${relation.supplier_name}的验证记录`} className="supplier-verification-panel">
          <div>
            <strong>{relation.supplier_name} · 定向验证</strong>
            <span className="muted"> 共 {verifications.length} 条</span>
          </div>
          {loading ? <p className="status-line">正在加载验证记录...</p> : null}
          {error ? <p className="error-text" role="alert">{error}</p> : null}
          {!loading && !error && verifications.length === 0 ? (
            <p className="muted">尚无定向验证记录。</p>
          ) : null}
          {!loading && !error
            ? verifications.map((verification) => (
                <article className="supplier-verification-item" key={verification.id}>
                  <div className="supplier-verification-head">
                    <span className="badge">{verification.verdict}</span>
                    {verification.confidence ? <span>可信度：{verification.confidence}</span> : null}
                    {verification.supply_content ? <span>验证供应：{verification.supply_content}</span> : null}
                  </div>
                  {verification.evidence_urls.length ? (
                    <div className="supplier-verification-links">
                      {verification.evidence_urls.map((url, index) => (
                        <ExternalLink href={url} key={url}>
                          {index === 0
                            ? `查看验证来源（${verification.evidence_urls.length}）`
                            : `验证来源 ${index + 1}`}
                        </ExternalLink>
                      ))}
                    </div>
                  ) : null}
                  {verification.evidence_md ? (
                    <details className="snippet-expand">
                      <summary>查看完整判定证据</summary>
                      <EvidenceText text={verification.evidence_md} />
                    </details>
                  ) : null}
                </article>
              ))
            : null}
        </section>
      </td>
    </tr>
  );
}

export function SupplierPage() {
  const [overview, setOverview] = useState<SupplierOverview | null>(null);
  const [relations, setRelations] = useState<Page<SupplierRelation> | null>(null);
  const [expandedRelation, setExpandedRelation] = useState<SupplierRelation | null>(null);
  const [verifications, setVerifications] = useState<SupplierVerification[]>([]);
  const [query, setQuery] = useState('');
  const [appliedQuery, setAppliedQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [overviewLoading, setOverviewLoading] = useState(true);
  const [listLoading, setListLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [listError, setListError] = useState('');
  const [detailError, setDetailError] = useState('');

  useEffect(() => {
    let ignore = false;
    getSupplierOverview()
      .then((result) => !ignore && setOverview(result))
      .catch(() => !ignore && setOverview(null))
      .finally(() => !ignore && setOverviewLoading(false));
    return () => { ignore = true; };
  }, []);

  useEffect(() => {
    let ignore = false;
    setListLoading(true);
    setListError('');
    listSupplierRelations({ limit: PAGE_SIZE, offset, q: appliedQuery || undefined })
      .then((result) => !ignore && setRelations(result))
      .catch((reason: unknown) => {
        if (!ignore) setListError(formatErrorMessage('无法加载供应关系', reason));
      })
      .finally(() => !ignore && setListLoading(false));
    return () => { ignore = true; };
  }, [appliedQuery, offset]);

  useEffect(() => {
    if (!expandedRelation) {
      setVerifications([]);
      return;
    }
    let ignore = false;
    setDetailLoading(true);
    setDetailError('');
    listSupplierVerifications(expandedRelation.id)
      .then((result) => !ignore && setVerifications(result))
      .catch((reason: unknown) => {
        if (!ignore) setDetailError(formatErrorMessage('无法加载验证记录', reason));
      })
      .finally(() => !ignore && setDetailLoading(false));
    return () => { ignore = true; };
  }, [expandedRelation]);

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setOffset(0);
    setAppliedQuery(query.trim());
  }

  function toggleVerification(relation: SupplierRelation) {
    setExpandedRelation((current) => (current?.id === relation.id ? null : relation));
  }

  const canGoPrevious = offset > 0;
  const canGoNext = relations ? offset + relations.limit < relations.total : false;

  return (
    <section className="supplier-page">
      <div className="page-title">
        <p className="eyebrow">供应链情报</p>
        <h1>供应关系</h1>
        <p className="muted">展示供应商、供应模块、关系提取来源与定向验证可信度。</p>
      </div>

      <section className="supplier-manufacturer-bar" aria-label="厂商范围">
        <label htmlFor="supplier-manufacturer">厂商选择</label>
        <select id="supplier-manufacturer" value="大疆" disabled>
          <option value="大疆">大疆（目前只支持大疆）</option>
        </select>
        {!overviewLoading && overview ? (
          <span className="muted">当前已收录 {overview.total_suppliers} 家供应商</span>
        ) : null}
      </section>

      <section className="supplier-relations-section">
        <form className="supplier-search-bar" onSubmit={submitSearch}>
          <label className="sr-only" htmlFor="supplier-query">搜索供应商或模块</label>
          <input
            id="supplier-query"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索供应商或供应零件/模块"
            value={query}
          />
          <button type="submit">搜索</button>
        </form>

        {listError ? <p className="error-text" role="alert">{listError}</p> : null}
        {listLoading ? <p className="status-line">正在加载供应关系...</p> : null}
        {relations && relations.items.length === 0 ? <p className="empty-state">没有匹配的供应关系。</p> : null}

        {relations && relations.items.length ? (
          <div className="supplier-table-scroll">
            <table className="supplier-relation-table">
              <thead>
                <tr>
                  <th scope="col">供应商</th>
                  <th scope="col">供应零件/模块</th>
                  <th scope="col">供应关系来源</th>
                  <th scope="col">
                    供应关系验证可信度
                    <small>绿色：明确 · 黄色：疑似 · 红色：不相关</small>
                  </th>
                </tr>
              </thead>
              <tbody>
                {relations.items.map((relation) => {
                  const expanded = expandedRelation?.id === relation.id;
                  const firstSource = relation.source_urls[0];
                  return (
                    <Fragment key={relation.id}>
                      <tr>
                        <td><strong>{relation.supplier_name}</strong></td>
                        <td>{relation.supply_content || '—'}</td>
                        <td>
                          {firstSource ? (
                            <ExternalLink href={firstSource}>
                              {`查看提取来源（${relation.source_urls.length}）`}
                            </ExternalLink>
                          ) : <span className="muted">暂无来源</span>}
                        </td>
                        <td>
                          <button
                            aria-expanded={expanded}
                            aria-label={`${expanded ? '收起' : '展开'}${relation.supplier_name}的验证记录`}
                            className={`verification-confidence ${verificationConfidenceClass(
                              relation.latest_verification_confidence,
                            )}`}
                            onClick={() => toggleVerification(relation)}
                            type="button"
                          >
                            {expanded ? '收起验证' : relation.latest_verification_confidence || '未验证'}
                          </button>
                        </td>
                      </tr>
                      {expanded ? (
                        <VerificationExpansion
                          error={detailError}
                          key={`${relation.id}-verification`}
                          loading={detailLoading}
                          relation={relation}
                          verifications={verifications}
                        />
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}

        {relations ? (
          <div className="button-row supplier-pagination">
            <span className="muted">共 {relations.total} 条关系</span>
            <button disabled={!canGoPrevious} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} type="button">上一页</button>
            <button disabled={!canGoNext} onClick={() => setOffset(offset + PAGE_SIZE)} type="button">下一页</button>
          </div>
        ) : null}
      </section>
    </section>
  );
}

export default SupplierPage;