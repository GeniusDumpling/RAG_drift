import { FormEvent, useEffect, useState } from 'react';

import { documentAssetUrl, getProcessedDocument, listDocumentModels, listDocumentRecords, listProcessedDocuments } from '../api/client';
import type { DocumentModel, DocumentRecord, DocumentSummary, Page, ProcessedDocumentDetail } from '../api/types';
import { formatErrorMessage } from '../utils/errors';
import { safeExternalHref } from '../utils/links';
import './DocumentLibraryPanel.css';

const TABS = [
  ['chunks', '检索切片'], ['elements', '正文与元素'], ['tables', '表格'],
  ['figures', '图片与说明'], ['parameters', '参数与脚注'], ['pages', '原文页面'],
] as const;
type Tab = typeof TABS[number][0];
const statusLabel = (status: string | null) => ({ ready: '已发布', pending_index: '等待向量化', indexing: '向量化中', failed: '处理失败' }[status || ''] || status || '等待处理');
const typeLabel = (kind: string) => ({ text: '正文', paragraph: '段落', heading: '标题', table: '表格', figure: '图片', figure_description: '图片说明', parameter: '参数', footnote: '脚注', specification: '技术参数', specifications: '技术参数', technical_specifications: '技术参数', user_manual: '用户手册', quick_start: '快速入门', shared_reference: '共用资料', safety_guidelines: '安全说明', release_notes: '更新记录' }[kind] || kind);

export function DocumentLibraryPanel() {
  const [models, setModels] = useState<DocumentModel[]>([]);
  const [model, setModel] = useState('');
  const [query, setQuery] = useState('');
  const [applied, setApplied] = useState('');
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [documents, setDocuments] = useState<Page<DocumentSummary>>();
  const [selected, setSelected] = useState('');
  const [error, setError] = useState('');
  const [modelError, setModelError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let ignore = false;
    listDocumentModels().then(result => { if (!ignore) setModels(result.items); })
      .catch(reason => { if (!ignore) setModelError(formatErrorMessage('无法加载文档型号', reason)); });
    return () => { ignore = true; };
  }, [revision]);

  useEffect(() => {
    let ignore = false;
    setLoading(true); setError(''); setDocuments(undefined);
    listProcessedDocuments({ q: applied, model, offset }).then(page => {
      if (!ignore) {
        setDocuments(page);
        setSelected(current => page.items.some(item => item.id === current) ? current : page.items[0]?.id || '');
      }
    }).catch(reason => {
      if (!ignore) { setError(formatErrorMessage('无法加载处理后文档', reason)); setSelected(''); }
    }).finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; };
  }, [applied, model, offset, revision]);

  function search(event: FormEvent) {
    event.preventDefault(); setApplied(query.trim()); setOffset(0); setSelected('');
  }

  return <section className="document-library" aria-label="处理后文档">
    <div className="card">
      <div className="card-header">
        <div><h2>文档处理数据</h2><p className="muted compact">查看已入库的中文内容、原文位置及质量记录。尚未导入数据库的任务会在入库后出现。</p></div>
        <button type="button" disabled={loading} onClick={() => { setModelError(''); setRevision(v => v + 1); }}>刷新文档</button>
      </div>
      <form className="document-filters" onSubmit={search}>
        <label>关联型号<select aria-label="文档关联型号" value={model} onChange={event => { setModel(event.target.value); setOffset(0); setSelected(''); }}>
          <option value="">全部型号</option>
          {models.map(item => <option key={item.slug} value={item.slug}>{item.manufacturer} · {item.name}</option>)}
        </select></label>
        <label>文档搜索<input aria-label="文档搜索" placeholder="文档标题、厂商或型号" maxLength={200} value={query} onChange={event => setQuery(event.target.value)} /></label>
        <button type="submit">搜索文档</button>
      </form>
      {modelError ? <p role="alert" className="error-text">{modelError}</p> : null}
      {error ? <p role="alert" className="error-text">{error}</p> : null}
      {loading ? <p role="status">正在加载文档列表…</p> : null}
    </div>
    <div className="document-workspace">
      <aside className="card document-list" aria-label="文档列表">
        <h3>文档列表{documents ? `（${documents.total}）` : ''}</h3>
        {documents?.items.map(item => <button key={item.id} type="button" className={`document-option${selected === item.id ? ' selected' : ''}`} aria-pressed={selected === item.id} onClick={() => setSelected(item.id)}>
          <strong>{item.title}</strong>
          <span>{typeLabel(item.document_type)} · {statusLabel(item.status)}</span>
          <span className="muted">{item.models.map(m => m.name).join('、') || '未关联型号'}</span>
        </button>)}
        {documents && !documents.items.length ? <p className="empty-state">没有匹配的文档。</p> : null}
        {documents ? <Pagination offset={offset} limit={documents.limit} total={documents.total} onChange={value => { setOffset(value); setSelected(''); }} /> : null}
      </aside>
      <div className="document-inspector">
        {selected ? <DocumentInspection key={selected} id={selected} revision={revision} /> : <p className="card muted">选择一份文档查看处理结果。</p>}
      </div>
    </div>
  </section>;
}

function DocumentInspection({ id, revision }: { id: string; revision: number }) {
  const [processingId, setProcessingId] = useState<string>();
  const [detail, setDetail] = useState<ProcessedDocumentDetail>();
  const [error, setError] = useState('');
  const [tab, setTab] = useState<Tab>('chunks');
  const [sourcePage, setSourcePage] = useState<number>();

  useEffect(() => {
    let ignore = false;
    setDetail(undefined); setError('');
    getProcessedDocument(id, processingId).then(result => { if (!ignore) setDetail(result); })
      .catch(reason => { if (!ignore) setError(formatErrorMessage('无法加载文档详情', reason)); });
    return () => { ignore = true; };
  }, [id, processingId, revision]);

  if (error) return <p className="card error-text" role="alert">{error}</p>;
  if (!detail) return <p className="card" role="status">正在加载文档详情…</p>;
  const source = safeExternalHref(detail.document.source_url);
  return <>
    <section className="card">
      <h2>{detail.document.title}</h2>
      <p className="muted">{detail.models.map(m => `${m.manufacturer} · ${m.name}`).join(' / ')}</p>
      <div className="tag-row"><span className="badge">{typeLabel(detail.document.document_type)}</span><span className="badge">{statusLabel(detail.processing.status)}</span><span className="badge">{detail.processing.id === detail.document.active_processing_id ? '当前发布版本' : '未发布或历史版本'}</span></div>
      <label className="document-version">处理版本<select aria-label="文档处理版本" value={detail.processing.id} onChange={event => setProcessingId(event.target.value)}>
        {detail.processings.map(p => <option key={p.id} value={p.id}>{p.chunker_version} · {statusLabel(p.status)} · {new Date(p.created_at).toLocaleString()} · {p.id.slice(0, 8)}{p.id === detail.document.active_processing_id ? '（当前发布）' : ''}</option>)}
      </select></label>
      <dl className="kv-grid">
        <div><dt>官方版本</dt><dd>{detail.document.official_version || '未标注'}</dd></div>
        <div><dt>语言策略</dt><dd>{detail.processing.language_policy}</dd></div>
        <div><dt>中文筛选页 / 原始页</dt><dd>{detail.stats.chinese_pages} / {detail.stats.pages}</dd></div>
        <div><dt>已向量化 / 检索切片</dt><dd>{detail.stats.indexed_chunks} / {detail.stats.chunks}</dd></div>
        <div><dt>表格 / 图片</dt><dd>{detail.stats.tables} / {detail.stats.figures}</dd></div>
        <div><dt>隔离的图片说明</dt><dd>{detail.stats.quarantined_figures}</dd></div>
      </dl>
      {detail.processing.status !== 'ready' ? <p className="document-notice">此版本尚未发布，内容或向量索引可能未完成；刷新后可查看最新状态。</p> : null}
      {source ? <a href={source} target="_blank" rel="noreferrer">官方原文</a> : null}
      <details><summary>文档与处理元数据</summary><pre>{JSON.stringify({ document: detail.document.metadata_json, processing: detail.processing }, null, 2)}</pre></details>
    </section>
    <div className="document-tabs" role="tablist" aria-label="文档数据分类">
      {TABS.map(([value, label]) => <button key={value} type="button" role="tab" id={`document-tab-${value}`} aria-controls="document-records" aria-selected={tab === value} onClick={() => { setTab(value); setSourcePage(undefined); }}>{label}</button>)}
    </div>
    <section id="document-records" role="tabpanel" aria-labelledby={`document-tab-${tab}`}>
      <DocumentRecords key={`${detail.processing.id}-${tab}-${revision}-${sourcePage ?? ''}`} id={id} processingId={detail.processing.id} tab={tab} initialPage={sourcePage} onOpenPage={page => { setTab('pages'); setSourcePage(page); }} />
    </section>
  </>;
}

function DocumentRecords({ id, processingId, tab, initialPage, onOpenPage }: {
  id: string; processingId: string; tab: Tab; initialPage?: number; onOpenPage: (page: number) => void;
}) {
  const [pageNo, setPageNo] = useState(initialPage ? String(initialPage) : '');
  const [appliedPage, setAppliedPage] = useState(initialPage);
  const [kind, setKind] = useState('');
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<Page<DocumentRecord>>();
  const [error, setError] = useState('');
  const section = tab === 'chunks' || tab === 'pages' ? tab : 'elements';
  const elementKind = tab === 'tables' ? 'table' : tab === 'figures' ? 'figure' : tab === 'parameters' ? kind || 'parameter' : kind;
  useEffect(() => {
    let ignore = false;
    setData(undefined); setError('');
    listDocumentRecords(id, section, { processing_id: processingId, kind: elementKind, page_no: appliedPage, offset })
      .then(result => { if (!ignore) setData(result); })
      .catch(reason => { if (!ignore) setError(formatErrorMessage('无法加载文档数据', reason)); });
    return () => { ignore = true; };
  }, [id, processingId, section, elementKind, appliedPage, offset]);
  return <div className="card">
    <form className="document-record-filters" onSubmit={event => { event.preventDefault(); setAppliedPage(pageNo ? Number(pageNo) : undefined); setOffset(0); }}>
      <label>原文页码<input aria-label="筛选原文页码" type="number" min={1} step={1} placeholder="全部页" value={pageNo} onChange={event => setPageNo(event.target.value)} /></label>
      <button type="submit">筛选页码</button>
      {tab === 'elements' || tab === 'parameters' ? <label>元素类型<select aria-label="文档元素类型" value={kind} onChange={event => { setKind(event.target.value); setOffset(0); }}>
        {tab === 'parameters' ? <><option value="">参数</option><option value="footnote">脚注</option></> : <><option value="">全部元素</option>{['heading', 'paragraph', 'table', 'figure', 'figure_description', 'parameter', 'footnote'].map(k => <option key={k} value={k}>{typeLabel(k)}</option>)}</>}
      </select></label> : null}
    </form>
    {error ? <p role="alert" className="error-text">{error}</p> : !data ? <p role="status">正在加载文档数据…</p> : <>
      <p className="muted">共 {data.total} 条记录</p>
      {!data.items.length ? <p className="empty-state">此版本没有匹配的记录。仅有技术参数的 HTML 文档没有 PDF 原文页面。</p> : null}
      <div className={tab === 'pages' ? 'document-page-grid' : 'document-record-list'}>
        {data.items.map(record => <DocumentRecordCard key={record.id} record={record} tab={tab} onOpenPage={onOpenPage} />)}
      </div>
      <Pagination offset={offset} limit={data.limit} total={data.total} onChange={setOffset} />
    </>}
  </div>;
}

function DocumentRecordCard({ record, tab, onOpenPage }: { record: DocumentRecord; tab: Tab; onOpenPage: (page: number) => void }) {
  if (tab === 'pages') return <article className="document-record">
    <h3>原文第 {record.source_page_no} 页</h3>
    <p>{record.included ? '已纳入中文内容' : '未纳入中文索引'} · {record.language_state}</p>
    <p className="muted">{record.inclusion_reason}</p>
    {record.render_asset_id ? <AssetImage id={record.render_asset_id} label={`原文第 ${record.source_page_no} 页`} /> : <p className="muted">此页没有保存渲染图，可从官方原文查看。</p>}
  </article>;
  const metadata = record.metadata_json || {};
  const description = metadata.description as { description_zh?: string; visible_labels?: string[]; uncertainties?: string[] } | undefined;
  const gate = metadata.quality_gate as { status?: string; verification?: string } | undefined;
  return <article className="document-record">
    <div className="card-header"><h3>{tab === 'chunks' ? `切片 ${record.chunk_index}` : typeLabel(record.element_type || '')}</h3><span className="badge">{record.embed_status || gate?.status || record.chunk_type || record.element_type}</span></div>
    <p className="muted compact">{record.section_path}</p>
    {record.regions?.length ? <div className="document-source-pages">{[...new Set(record.regions.map(r => r.source_page_no))].map(page => <button key={page} type="button" className="link-button" onClick={() => onOpenPage(page)}>原文第 {page} 页</button>)}</div> : null}
    {Array.isArray(metadata.source_pages) ? <div className="document-source-pages">{(metadata.source_pages as number[]).map(page => <button key={page} type="button" className="link-button" onClick={() => onOpenPage(page)}>原文第 {page} 页</button>)}</div> : null}
    {record.table_json?.rows?.length ? <div className="table-scroll"><table className="data-table"><tbody>{record.table_json.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{cell == null ? '' : typeof cell === 'object' ? JSON.stringify(cell) : String(cell)}</td>)}</tr>)}</tbody></table></div> : record.display_text || record.text ? <pre className="document-text">{record.display_text || record.text}</pre> : null}
    {record.asset_id ? <AssetImage id={record.asset_id} label={typeLabel(record.element_type || 'figure')} /> : null}
    {gate?.status === 'quarantined' ? <p className="document-notice">该图片说明未通过质量检查，已隔离；请结合原图和正文查看。</p> : null}
    {description ? <div className="document-caption"><h4>自动生成的图片说明</h4><p>{description.description_zh || '没有可用的图片说明。'}</p><p className="muted">核验方式：{gate?.verification === 'vision_grounding' ? '图像复核' : '结构检查'}；说明需结合原图核验。</p>{description.visible_labels?.length ? <p>可见标注：{description.visible_labels.join('、')}</p> : null}{description.uncertainties?.length ? <p>不确定项：{description.uncertainties.join('；')}</p> : null}</div> : null}
    <details><summary>元数据、脚注与来源记录</summary><pre>{JSON.stringify({ id: record.id, regions: record.regions, ...metadata }, null, 2)}</pre></details>
    {record.embed_text ? <details><summary>向量化输入文本</summary><pre>{record.embed_text}</pre></details> : null}
  </article>;
}

function AssetImage({ id, label }: { id: string; label: string }) {
  const [failed, setFailed] = useState(false);
  return failed ? <p className="muted">图片文件暂不可用，请查看官方原文。</p> : <a className="document-asset" href={documentAssetUrl(id)} target="_blank" rel="noreferrer"><img src={documentAssetUrl(id)} alt={label} loading="lazy" onError={() => setFailed(true)} /></a>;
}

function Pagination({ offset, limit, total, onChange }: { offset: number; limit: number; total: number; onChange: (offset: number) => void }) {
  return <div className="button-row document-pagination"><button type="button" disabled={!offset} onClick={() => onChange(Math.max(0, offset - limit))}>上一页</button><span className="muted">{total ? `${offset + 1}–${Math.min(offset + limit, total)} / ${total}` : '0 / 0'}</span><button type="button" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>下一页</button></div>;
}
