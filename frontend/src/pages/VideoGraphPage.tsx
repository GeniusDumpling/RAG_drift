import { FormEvent, useEffect, useMemo, useState } from 'react';

import { getVideoGraph, getVideoGraphEvidence, getVideoGraphOverview, getVideoGraphStatus, listContents } from '../api/client';
import type { ContentListItem, Page, VideoGraphData, VideoGraphEdge, VideoGraphEvidence, VideoGraphNode, VideoGraphOverview, VideoGraphStatus } from '../api/types';
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
const STATUS_LABELS: Record<string, string> = {
  not_queued: '尚未构图', pending: '等待处理', running: '正在处理', success: '处理完成',
  failed: '处理失败', obsolete: '版本已过期', withdrawn: '已撤回',
};

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
  const [videos, setVideos] = useState<Page<ContentListItem> | null>(null);
  const [videoOffset, setVideoOffset] = useState(0);
  const [videoSearch, setVideoSearch] = useState('');
  const [appliedVideoSearch, setAppliedVideoSearch] = useState('');
  const [videoId, setVideoId] = useState('');
  const [videoStatus, setVideoStatus] = useState<VideoGraphStatus | null>(null);
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
  const [videoError, setVideoError] = useState('');
  const [evidenceError, setEvidenceError] = useState('');

  useEffect(() => {
    let ignore = false;
    getVideoGraphOverview().then((value) => { if (!ignore) setOverview(value); }).catch(() => { if (!ignore) setOverview(null); });
    return () => { ignore = true; };
  }, [refresh]);

  useEffect(() => {
    let ignore = false;
    setVideos(null); setVideoError('');
    listContents({ item_type: 'video_description', q: appliedVideoSearch || undefined, limit: 10, offset: videoOffset })
      .then((value) => { if (!ignore) setVideos(value); })
      .catch((reason: unknown) => { if (!ignore) setVideoError(formatErrorMessage('无法加载视频', reason)); });
    return () => { ignore = true; };
  }, [videoOffset, appliedVideoSearch, refresh]);

  useEffect(() => {
    let ignore = false;
    setLoading(true); setError(''); setGraph(null); setSelectedEdge(null); setEvidence(null); setEvidenceOffset(0);
    getVideoGraph({ content_item_id: videoId || undefined, entity_id: entityId || undefined,
      q: appliedQuery || undefined, entity_type: entityType || undefined, assertion: assertion || undefined,
      hops, max_nodes: 80, max_edges: 160 })
      .then((value) => { if (!ignore) setGraph(value); })
      .catch((reason: unknown) => { if (!ignore) setError(formatErrorMessage('无法加载知识图谱', reason)); })
      .finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; };
  }, [videoId, entityId, entityType, assertion, hops, appliedQuery, refresh]);

  useEffect(() => {
    let ignore = false;
    setVideoStatus(null);
    if (videoId) getVideoGraphStatus(videoId)
      .then((value) => { if (!ignore) setVideoStatus(value); })
      .catch((reason: unknown) => { if (!ignore) setVideoError(formatErrorMessage('无法读取视频构图状态', reason)); });
    return () => { ignore = true; };
  }, [videoId, refresh]);

  useEffect(() => {
    let ignore = false;
    setEvidence(null); setEvidenceError(''); setEvidenceLoading(false);
    if (selectedEdge?.kind === 'relation') {
      setEvidenceLoading(true);
      getVideoGraphEvidence(selectedEdge.id, { content_item_id: videoId || undefined,
        assertion: assertion || undefined, limit: 20, offset: evidenceOffset })
        .then((value) => { if (!ignore) setEvidence(value); })
        .catch((reason: unknown) => { if (!ignore) setEvidenceError(formatErrorMessage('无法加载关系证据', reason)); })
        .finally(() => { if (!ignore) setEvidenceLoading(false); });
    }
    return () => { ignore = true; };
  }, [selectedEdge, videoId, assertion, evidenceOffset, refresh]);

  function selectEdge(edge: VideoGraphEdge) {
    if (edge.kind === 'mention') { if (videoId) onOpenContent(videoId); return; }
    setSelectedEdge(edge); setEvidenceOffset(0);
  }
  function selectVideo(id: string) { setVideoId(id); setEntityId(''); }
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
      </div> : null}
      <div className="video-graph-layout">
        <aside className="video-graph-video-list" aria-label="按视频浏览">
          <h2>按视频浏览</h2>
          <button type="button" className={!videoId ? 'active' : ''} onClick={() => selectVideo('')}>全部视频图谱</button>
          <form onSubmit={(event) => { event.preventDefault(); setAppliedVideoSearch(videoSearch.trim()); setVideoOffset(0); }}>
            <label htmlFor="graph-video-query">查找视频</label><input id="graph-video-query" value={videoSearch} onChange={(event) => setVideoSearch(event.target.value)} />
            <button type="submit">查找</button>
          </form>
          {videoError ? <p role="alert" className="error-text">{videoError}</p> : null}
          {videos ? videos.items.map((video) => <button type="button" key={video.id}
            className={videoId === video.id ? 'active' : ''} onClick={() => selectVideo(video.id)}>{video.title || '未命名视频'}</button>) : !videoError ? <p className="muted">正在加载视频…</p> : null}
          {videos ? <div className="button-row"><button type="button" disabled={videoOffset === 0} onClick={() => setVideoOffset(Math.max(0, videoOffset - 10))}>上一页</button>
            <button type="button" disabled={videoOffset + 10 >= videos.total} onClick={() => setVideoOffset(videoOffset + 10)}>下一页</button></div> : null}
          {videoStatus ? <div className="video-graph-status"><strong>{STATUS_LABELS[videoStatus.status] ?? videoStatus.status}</strong>
            <p>字幕切片：{videoStatus.transcript_chunks}</p>
            {videoStatus.summary_only ? <p>仅有摘要，展示实体提及。</p> : null}
            {videoStatus.source_changed ? <p>材料已变化，当前仍显示上次成功构建的图谱。</p> : null}
            {videoStatus.assessment ? <p>{videoStatus.assessment.reason}</p> : null}
            {videoStatus.error ? <p className="error-text">{videoStatus.error}</p> : null}
          </div> : null}
        </aside>
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
            <p className="muted">{graph.nodes.length} 个节点 · {graph.edges.length} 条连接。点击实体聚焦；实线表示关系，虚线表示视频提及。</p>
            {graph.truncated ? <p className="status-line">已限制显示范围，请搜索实体或选择视频查看局部图谱。</p> : null}
            <GraphCanvas graph={graph} focusId={entityId || (videoId ? `video:${videoId}` : '')} selectedEdge={selectedEdge?.id ?? ''}
              onNode={(node) => { if (node.kind === 'entity') setEntityId(node.id); else if (node.content_item_id) onOpenContent(node.content_item_id); }} onEdge={selectEdge} />
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
      </div>
    </section>
  );
}
