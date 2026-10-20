import { FormEvent, useEffect, useMemo, useState } from 'react';

import { getVideoGraph, getVideoGraphEvidence, getVideoGraphOverview } from '../api/client';
import type { Page, VideoGraphData, VideoGraphEdge, VideoGraphEvidence, VideoGraphNode, VideoGraphOverview } from '../api/types';
import { formatErrorMessage } from '../utils/errors';
import { safeExternalHref } from '../utils/links';

const ENTITY_LABELS: Record<string, string> = {
  organization: '企业／品牌', product: '产品', component: '零部件', software: '软件',
  protocol: '协议', technology: '技术', method: '方法', fault: '故障',
};
const PREDICATE_LABELS: Record<string, string> = {
  uses: '使用', contains: '包含', compatible_with: '兼容', applied_to: '应用于',
  reports_fault: '报告故障', mitigates: '缓解', mentions: '提及',
};
const ASSERTION_LABELS: Record<string, string> = { asserted: '陈述', uncertain: '推测', negated: '否定' };

function secondsLabel(value: number | null): string {
  if (value === null) return '时间未知';
  const seconds = Math.floor(value);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function GraphCanvas({ graph, focusId, selectedEdge, onNode, onEdge }: {
  graph: VideoGraphData; focusId: string; selectedEdge: string;
  onNode: (node: VideoGraphNode) => void; onEdge: (edge: VideoGraphEdge) => void;
}) {
  const [zoom, setZoom] = useState(1);
  const positions = useMemo(() => {
    const points = new Map<string, { x: number; y: number }>();
    const center = graph.nodes.find((node) => node.id === focusId) ?? graph.nodes.find((node) => node.kind === 'video');
    const others = graph.nodes.filter((node) => node.id !== center?.id);
    if (center) points.set(center.id, { x: 500, y: 330 });
    others.forEach((node, index) => {
      const inner = index < 22;
      const ringCount = inner ? Math.min(22, others.length) : others.length - 22;
      const ringIndex = inner ? index : index - 22;
      const angle = (2 * Math.PI * ringIndex) / Math.max(1, ringCount) - Math.PI / 2;
      const radiusX = inner ? 255 : 435;
      const radiusY = inner ? 185 : 285;
      points.set(node.id, { x: 500 + Math.cos(angle) * radiusX, y: 330 + Math.sin(angle) * radiusY });
    });
    return points;
  }, [graph.nodes, focusId]);

  return (
    <div className="video-graph-canvas-wrap">
      <div className="button-row video-graph-zoom" aria-label="图谱缩放">
        <button type="button" onClick={() => setZoom((value) => Math.min(2, value + 0.2))}>放大</button>
        <button type="button" onClick={() => setZoom((value) => Math.max(0.6, value - 0.2))}>缩小</button>
        <button type="button" onClick={() => setZoom(1)}>重置</button>
      </div>
      <svg className="video-graph-canvas" aria-label="视频知识图谱" role="group"
        viewBox={`${500 - 500 / zoom} ${330 - 330 / zoom} ${1000 / zoom} ${660 / zoom}`}>
        <defs><marker id="video-graph-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>
        {graph.edges.map((edge) => {
          const source = positions.get(edge.source), target = positions.get(edge.target);
          if (!source || !target) return null;
          const dx = target.x - source.x, dy = target.y - source.y;
          const length = Math.hypot(dx, dy) || 1;
          return (
            <g key={edge.id} className={`video-graph-edge ${edge.kind}${selectedEdge === edge.id ? ' selected' : ''}`}
              role="button" tabIndex={0} aria-label={`${PREDICATE_LABELS[edge.predicate] ?? edge.predicate}，${edge.evidence_count}条证据`}
              onClick={() => onEdge(edge)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onEdge(edge); } }}>
              <title>{PREDICATE_LABELS[edge.predicate] ?? edge.predicate} · {edge.evidence_count} 条证据</title>
              <line x1={source.x + dx / length * 27} y1={source.y + dy / length * 27}
                x2={target.x - dx / length * 30} y2={target.y - dy / length * 30} markerEnd="url(#video-graph-arrow)" />
              <text x={(source.x + target.x) / 2} y={(source.y + target.y) / 2 - 6} textAnchor="middle">{PREDICATE_LABELS[edge.predicate] ?? edge.predicate}</text>
            </g>
          );
        })}
        {graph.nodes.map((node) => {
          const point = positions.get(node.id);
          if (!point) return null;
          return (
            <g key={node.id} transform={`translate(${point.x},${point.y})`}
              className={`video-graph-node ${node.kind} ${node.entity_type ?? ''}${focusId === node.id ? ' selected' : ''}`}
              role="button" tabIndex={0} aria-label={node.label}
              onClick={() => onNode(node)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onNode(node); } }}>
              <title>{node.label}</title><circle r="25" />
              <text textAnchor="middle" y="4">{node.kind === 'video' ? '视频' : ENTITY_LABELS[node.entity_type ?? ''] ?? '实体'}</text>
              <text className="video-graph-node-label" textAnchor="middle" y="43">{node.label.length > 18 ? `${node.label.slice(0, 18)}…` : node.label}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function VideoGraphPage({ onOpenContent }: { onOpenContent: (id: string) => void }) {
  const [overview, setOverview] = useState<VideoGraphOverview | null>(null);
  const [graph, setGraph] = useState<VideoGraphData | null>(null);
  const [query, setQuery] = useState('');
  const [appliedQuery, setAppliedQuery] = useState('');
  const [entityId, setEntityId] = useState('');
  const [entityType, setEntityType] = useState('');
  const [assertion, setAssertion] = useState('');
  const [hops, setHops] = useState(1);
  const [refresh, setRefresh] = useState(0);
  const [selectedEdge, setSelectedEdge] = useState<VideoGraphEdge | null>(null);
  const [evidence, setEvidence] = useState<Page<VideoGraphEvidence> | null>(null);
  const [evidenceOffset, setEvidenceOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [error, setError] = useState('');
  const [evidenceError, setEvidenceError] = useState('');

  useEffect(() => {
    let ignore = false;
    getVideoGraphOverview().then((value) => { if (!ignore) setOverview(value); }).catch(() => { if (!ignore) setOverview(null); });
    return () => { ignore = true; };
  }, [refresh]);

  useEffect(() => {
    let ignore = false;
    setLoading(true); setError(''); setGraph(null); setSelectedEdge(null); setEvidence(null); setEvidenceOffset(0);
    getVideoGraph({ entity_id: entityId || undefined,
      q: appliedQuery || undefined, entity_type: entityType || undefined, assertion: assertion || undefined,
      hops, max_nodes: 80, max_edges: 160 })
      .then((value) => { if (!ignore) setGraph(value); })
      .catch((reason: unknown) => { if (!ignore) setError(formatErrorMessage('无法加载知识图谱', reason)); })
      .finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; };
  }, [entityId, entityType, assertion, hops, appliedQuery, refresh]);

  useEffect(() => {
    let ignore = false;
    setEvidence(null); setEvidenceError(''); setEvidenceLoading(false);
    if (selectedEdge?.kind === 'relation') {
      setEvidenceLoading(true);
      getVideoGraphEvidence(selectedEdge.id, {
        assertion: assertion || undefined, limit: 20, offset: evidenceOffset })
        .then((value) => { if (!ignore) setEvidence(value); })
        .catch((reason: unknown) => { if (!ignore) setEvidenceError(formatErrorMessage('无法加载关系证据', reason)); })
        .finally(() => { if (!ignore) setEvidenceLoading(false); });
    }
    return () => { ignore = true; };
  }, [selectedEdge, assertion, evidenceOffset, refresh]);

  function selectEdge(edge: VideoGraphEdge) {
    if (edge.kind !== 'relation') return;
    setSelectedEdge(edge); setEvidenceOffset(0);
  }
  function searchEntities(event: FormEvent) { event.preventDefault(); setAppliedQuery(query.trim()); setEntityId(''); }
  const labels = new Map(graph?.nodes.map((node) => [node.id, node.label]) ?? []);
  const relations = graph?.edges.filter((edge) => edge.kind === 'relation') ?? [];

  return (
    <section className="video-graph-page">
      <div className="page-title"><p className="eyebrow">视频情报</p><h1>视频知识图谱</h1>
        <p className="muted">浏览实体与关系，查看字幕原文及视频时间定位。陈述状态表示讲述者的表达，尚未经独立事实验证。</p></div>
      {overview ? <div className="video-graph-stats" aria-label="图谱统计">
        <span>视频 {overview.active_videos} / {overview.videos}</span><span>实体 {overview.entities}</span>
        <span>关系 {overview.relations}</span><span>证据 {overview.evidence}</span>
        <span>待处理 {overview.builds.pending ?? 0} · 失败 {overview.builds.failed ?? 0}</span>
        <span>部分完成 {overview.partial_videos ?? overview.builds.partial_success ?? 0}</span>
        {overview.chunk_coverage && overview.chunk_coverage.videos > 0 ? <span
          title={`已统计 ${overview.chunk_coverage.videos} 个视频的切片覆盖情况`}>
          切片通过 {overview.chunk_coverage.passed_chunks}/{overview.chunk_coverage.total_chunks}
          {' · '}跳过 {overview.chunk_coverage.skipped_chunks}
          {' · '}待处理 {overview.chunk_coverage.pending_chunks}
        </span> : null}
      </div> : null}
        <div className="video-graph-workspace">
          <form className="video-graph-toolbar" onSubmit={searchEntities}>
            <label>实体名称<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="例如 DJI、GPS、PX4" /></label>
            <label>实体类型<select value={entityType} onChange={(event) => { setEntityType(event.target.value); setEntityId(''); }}><option value="">全部</option>{Object.entries(ENTITY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>陈述状态<select value={assertion} onChange={(event) => setAssertion(event.target.value)}><option value="">全部</option>{Object.entries(ASSERTION_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>展开层数<select value={hops} onChange={(event) => setHops(Number(event.target.value))}><option value={1}>1 跳</option><option value={2}>2 跳</option></select></label>
            <button type="submit">搜索实体</button><button type="button" onClick={() => setRefresh((value) => value + 1)}>刷新</button>
            {entityId ? <button type="button" onClick={() => setEntityId('')}>取消实体聚焦</button> : null}
          </form>
          {error ? <p role="alert" className="error-text">{error}</p> : null}
          {loading ? <p className="status-line">正在加载图谱…</p> : null}
          {graph && graph.nodes.length === 0 ? <p className="empty-state">暂无匹配的图谱数据。视频处理完成后可在此浏览实体及字幕证据。</p> : null}
          {graph && graph.nodes.length > 0 ? <>
            <p className="muted">{graph.nodes.length} 个节点 · {graph.edges.length} 条连接。点击实体聚焦，点击关系查看字幕证据。</p>
            {graph.truncated ? <p className="status-line">已限制显示范围，请搜索实体查看局部图谱。</p> : null}
            <GraphCanvas graph={graph} focusId={entityId} selectedEdge={selectedEdge?.id ?? ''}
              onNode={(node) => { if (node.kind === 'entity') setEntityId(node.id); }} onEdge={selectEdge} />
          </> : null}
          <div className="video-graph-details">
            <section aria-label="关系列表"><h2>关系</h2>
              {relations.length === 0 ? <p className="muted">暂无字幕支持的关系。</p> : null}
              {relations.map((edge) => <button type="button" key={edge.id} className={selectedEdge?.id === edge.id ? 'active' : ''} onClick={() => selectEdge(edge)}>
                <strong>{labels.get(edge.source)} → {labels.get(edge.target)}</strong>
                <span>{PREDICATE_LABELS[edge.predicate] ?? edge.predicate} · {edge.evidence_count} 条证据</span>
                <small>{Object.entries(edge.assertions).filter(([, count]) => count > 0).map(([key, count]) => `${ASSERTION_LABELS[key] ?? key} ${count}`).join(' · ')}</small>
                {Object.keys(edge.qualifiers).length > 0 ? <small>{Object.entries(edge.qualifiers).map(([key, value]) => `${key}: ${value}`).join('；')}</small> : null}
              </button>)}
            </section>
            <section aria-label="字幕证据"><h2>字幕证据</h2>
              {!selectedEdge ? <p className="muted">选择关系查看原文和视频时间点。</p> : null}
              {evidenceLoading ? <p className="status-line">正在加载证据…</p> : null}
              {evidenceError ? <p role="alert" className="error-text">{evidenceError}</p> : null}
              {evidence?.items.map((item) => {
                const href = safeExternalHref(item.video_url);
                return <article className="video-graph-evidence" key={item.id}><strong>{item.title || '视频'}</strong>
                  <p>{ASSERTION_LABELS[item.assertion]} · 抽取把握程度 {Math.round(item.confidence * 100)}% · 字幕来源 {item.caption_source || '未知'}</p>
                  <blockquote>{item.quote}</blockquote>
                  {href ? <a href={href} target="_blank" rel="noreferrer">观看视频 {secondsLabel(item.start_seconds)} — {secondsLabel(item.end_seconds)}</a> : null}
                  <button type="button" onClick={() => onOpenContent(item.content_item_id)}>内容详情</button>
                </article>;
              })}
              {evidence ? <div className="button-row"><span>共 {evidence.total} 条证据</span>
                <button type="button" disabled={evidenceOffset === 0} onClick={() => setEvidenceOffset(Math.max(0, evidenceOffset - 20))}>上一页证据</button>
                <button type="button" disabled={evidenceOffset + 20 >= evidence.total} onClick={() => setEvidenceOffset(evidenceOffset + 20)}>下一页证据</button>
              </div> : null}
            </section>
          </div>
        </div>
    </section>
  );
}
