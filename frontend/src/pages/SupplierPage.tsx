import { FormEvent, Fragment, useEffect, useState } from 'react';

import {
  getSupplierOverview,
  listSupplierBrands,
  listSupplierRelations,
  listSupplierVerifications,
} from '../api/client';
import type {
  Page,
  SupplierOverview,
  SupplierBrand,
  SupplierRelation,
  SupplierVerification,
} from '../api/types';
import { formatErrorMessage } from '../utils/errors';
import { safeExternalHref } from '../utils/links';

const PAGE_SIZE = 50;
const EVIDENCE_URL_PATTERN = /https?:\/\/[^\s)\]}）]+/;

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
      {text.split('\n').map((line, index) => {
        const match = line.match(EVIDENCE_URL_PATTERN);
        const href = safeExternalHref(match?.[0]);
        if (!match || !href || match.index === undefined) return `${line}${index < text.split('\n').length - 1 ? '\n' : ''}`;
        const quote = line.slice(0, match.index).replace(/\s*[（(]\s*$/, '').trimEnd();
        const suffix = line.slice(match.index + match[0].length).replace(/^\s*[）)]/, '');
        return (
          <Fragment key={`${href}-${index}`}>
            <a href={href} rel="noreferrer" target="_blank">{quote || href}</a>
            {suffix}{index < text.split('\n').length - 1 ? '\n' : ''}
          </Fragment>
        );
      })}
    </pre>
  );
}

function EvidenceMetadata({ models = [], years = [] }: { models?: string[]; years?: number[] }) {
  if (!models.length && !years.length) return null;
  return (
    <div className="supplier-evidence-metadata">
      <span>机型：{models.length ? models.join('、') : '未注明'}</span>
      <span>证据年份：{years.length ? years.join('、') : '未注明'}</span>
    </div>
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
            <strong>{relation.buyer_name} · {relation.supplier_name} · 定向验证</strong>
            <span className="muted"> 共 {verifications.length} 条</span>
          </div>
          {loading ? <p className="status-line">正在加载验证记录...</p> : null}
          {error ? <p className="error-text" role="alert">{error}</p> : null}
          {!loading && !error && verifications.length === 0 ? (
            <p className="muted">尚无定向验证记录。</p>
          ) : null}
          {!loading && !error
            ? verifications.map((verification) => (
                <article key={verification.id} className="supplier-verification-item">
                  <div className="supplier-verification-head">
                    <strong>{verification.execution_status === 'failed' ? '执行失败' : verification.verdict}</strong>
                    {verification.confidence ? <span>{verification.confidence}</span> : null}
                    {verification.verify_time ? <time dateTime={verification.verify_time}>
                      {new Date(verification.verify_time).toLocaleString('zh-CN')}
                    </time> : null}
                  </div>
                  <EvidenceMetadata models={verification.drone_models} years={verification.evidence_years} />
                  {verification.evidence_md
                    ? <EvidenceText text={verification.evidence_md} />
                    : <p className="muted">该验证记录未提供判定依据。</p>}
                </article>
              ))
            : null}
        </section>
      </td>
    </tr>
  );
}

export function SupplierPage() {
  const [brands, setBrands] = useState<SupplierBrand[]>([]);
  const [buyerName, setBuyerName] = useState('大疆');
  const [brandError, setBrandError] = useState('');
  const [overviewError, setOverviewError] = useState('');
  const [refresh, setRefresh] = useState(0);
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
    setBrandError('');
    listSupplierBrands()
      .then((result) => {
        if (ignore) return;
        setBrands([...result].sort((a, b) =>
          a.buyer_name === '大疆' ? -1 : b.buyer_name === '大疆' ? 1 : a.buyer_name.localeCompare(b.buyer_name),
        ));
        if (!result.some((item) => item.buyer_name === '大疆')) {
          setBuyerName((current) => current === '大疆' ? (result[0]?.buyer_name ?? '') : current);
        }
      })
      .catch((reason: unknown) => !ignore && setBrandError(formatErrorMessage('无法加载品牌列表', reason)));
    return () => { ignore = true; };
  }, [refresh]);

  useEffect(() => {
    let ignore = false;
    setOverview(null);
    setOverviewLoading(true);
    setOverviewError('');
    getSupplierOverview(buyerName || undefined)
      .then((result) => !ignore && setOverview(result))
      .catch((reason: unknown) => !ignore && setOverviewError(formatErrorMessage('无法加载品牌统计', reason)))
      .finally(() => !ignore && setOverviewLoading(false));
    return () => { ignore = true; };
  }, [buyerName, refresh]);

  useEffect(() => {
    let ignore = false;
    setRelations(null);
    setListLoading(true);
    setListError('');
    listSupplierRelations({ buyer_name: buyerName || undefined, limit: PAGE_SIZE, offset, q: appliedQuery || undefined })
      .then((result) => !ignore && setRelations(result))
      .catch((reason: unknown) => {
        if (!ignore) setListError(formatErrorMessage('无法加载供应关系', reason));
      })
      .finally(() => !ignore && setListLoading(false));
    return () => { ignore = true; };
  }, [buyerName, appliedQuery, offset, refresh]);

  useEffect(() => {
    if (!expandedRelation) {
      setVerifications([]);
      setDetailLoading(false);
      setDetailError('');
      return;
    }
    let ignore = false;
    setVerifications([]);
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
    setExpandedRelation(null);
    setAppliedQuery(query.trim());
  }

  function changeBrand(value: string) {
    setRelations(null);
    setOverview(null);
    setExpandedRelation(null);
    setVerifications([]);
    setListError('');
    setDetailError('');
    setQuery('');
    setAppliedQuery('');
    setOffset(0);
    setBuyerName(value);
  }

  function toggleVerification(relation: SupplierRelation) {
    setVerifications([]);
    setDetailError('');
    setDetailLoading(true);
    setExpandedRelation((current) => (current?.id === relation.id ? null : relation));
  }

  const canGoPrevious = offset > 0;
  const canGoNext = relations ? offset + relations.limit < relations.total : false;

  return (
    <section className="supplier-page">
      <div className="page-title">
        <p className="eyebrow">供应链情报</p>
        <h1>供应关系</h1>
        <p className="muted">按无人机品牌查看供应商、供应模块、机型与原文验证证据。</p>
      </div>

      <section className="supplier-manufacturer-bar" aria-label="品牌范围">
        <label htmlFor="supplier-manufacturer">品牌选择</label>
        <select id="supplier-manufacturer" value={buyerName} onChange={(event) => changeBrand(event.target.value)}>
          <option value="">全部品牌</option>
          {buyerName && !brands.some((item) => item.buyer_name === buyerName)
            ? <option value={buyerName}>{buyerName}</option> : null}
          {brands.map((item) => <option key={item.buyer_name} value={item.buyer_name}>
            {item.buyer_name}（{item.total_suppliers}）
          </option>)}
        </select>
        <button type="button" className="secondary" onClick={() => {
          setExpandedRelation(null);
          setRefresh((current) => current + 1);
        }}>刷新</button>
        {overviewLoading ? <span className="muted" role="status">正在加载品牌统计...</span> : null}
        {!overviewLoading && overview ? (
          <div className="supplier-overview-summary" aria-label="品牌统计" aria-live="polite">
            <strong>{buyerName || '全部品牌'} · 已收录 {overview.total_suppliers} 家供应商</strong>
            <span>已验证 {overview.verified} · 待验证 {overview.unverified} · 验证失败 {overview.verify_failed}</span>
          </div>
        ) : null}
      </section>
      {brandError ? <p className="error-text" role="alert">{brandError}</p> : null}
      {overviewError ? <p className="error-text" role="alert">{overviewError}</p> : null}

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
                        <td><strong>{relation.supplier_name}</strong>
                          <span className="supplier-brand-name">{relation.buyer_name || '品牌未注明'}</span>
                        </td>
                        <td>{relation.supply_content || '—'}
                          <EvidenceMetadata models={relation.drone_models} years={relation.evidence_years} />
                        </td>
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
                            {expanded ? '收起验证' : relation.latest_verification_confidence || relation.verify_status}
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
            <button disabled={listLoading || !canGoPrevious} onClick={() => { setExpandedRelation(null); setOffset(Math.max(0, offset - PAGE_SIZE)); }} type="button">上一页</button>
            <button disabled={listLoading || !canGoNext} onClick={() => { setExpandedRelation(null); setOffset(offset + PAGE_SIZE); }} type="button">下一页</button>
          </div>
        ) : null}
      </section>
    </section>
  );
}

export default SupplierPage;
