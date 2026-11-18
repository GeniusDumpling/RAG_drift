import type { EvidenceObject } from '../api/types';
import { itemTypeLabel, safeExternalHref } from '../utils/links';
import { EvidenceImages } from './EvidenceImages';

const MAX_SNIPPET_LENGTH = 300;
const FORUM_CONDITION_LABELS = new Map([
  ['firmware', '固件'], ['firmware_version', '固件'],
  ['software', '软件'], ['software_version', '软件'],
  ['autopilot', '飞控'], ['hardware', '硬件'],
  ['vehicle', '载体'], ['vehicle_type', '载体'], ['mission_context', '任务场景'],
]);
const FORUM_ACT_LABELS = new Map([
  ['observation', '问题现象'], ['recommendation', '操作建议'],
  ['test_report', '尝试与反馈'], ['technical_statement', '技术陈述'],
  ['correction', '纠正'], ['question', '提问'], ['hypothesis', '推测'],
]);

function forumConditions(qualifiers: Record<string, unknown>): Array<[string, string]> {
  const values = new Map<string, [string, string]>();
  for (const [rawKey, rawValue] of Object.entries(qualifiers)) {
    if (rawKey === 'case_id' || rawKey === 'modality') continue;
    const key = rawKey.replace(/^context:/, '');
    const label = FORUM_CONDITION_LABELS.get(key) || key;
    const value = typeof rawValue === 'string' ? rawValue : JSON.stringify(rawValue);
    values.set(`${label}:${value}`, [label, value]);
  }
  return [...values.values()];
}

function truncate(text: string, maxLen: number): string {
  if (text.length <= maxLen) {
    return text;
  }
  return text.slice(0, maxLen) + '…';
}

function youtubeEmbedUrl(url: string | null): string | null {
  if (!url) {
    return null;
  }
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.toLowerCase().replace(/^www\./, '');
    let videoId = '';
    if (host === 'youtu.be') {
      videoId = parsed.pathname.slice(1).split('/')[0] || '';
    } else if (host === 'youtube.com') {
      videoId = parsed.searchParams.get('v') || '';
      if (!videoId && parsed.pathname.startsWith('/shorts/')) {
        videoId = parsed.pathname.split('/')[2] || '';
      }
    }
    return /^[A-Za-z0-9_-]{6,}$/.test(videoId)
      ? `https://www.youtube-nocookie.com/embed/${encodeURIComponent(videoId)}`
      : null;
  } catch {
    return null;
  }
}

type EvidenceCardProps = {
  evidence: EvidenceObject;
  onOpenContent?: (contentItemId: string) => void;
};

export function EvidenceCard({ evidence, onOpenContent }: EvidenceCardProps) {
  const canonicalHref = safeExternalHref(evidence.canonical_url);
  const videoHref = evidence.video_url ? safeExternalHref(evidence.video_url) : null;
  const youtubeEmbedHref = youtubeEmbedUrl(evidence.video_url);
  const hasLongSnippet = evidence.snippet.length > MAX_SNIPPET_LENGTH;
  const hasVideoDescription = Boolean(
    evidence.description_text && (evidence.item_type === 'video_description' || evidence.video_url),
  );
  const hasExpandableContent = hasLongSnippet || hasVideoDescription;

  return (
    <article className="card evidence-card">
      <div className="card-header">
        <div>
          <h3>{evidence.title || '未命名内容'}</h3>
          <p className="muted compact">
            {evidence.source_site_name} · {itemTypeLabel(evidence.item_type)}
          </p>
        </div>
        <span className="badge">{evidence.graph_evidence?.length || evidence.forum_graph_evidence?.length ? '图谱关联' : evidence.matched_by}</span>
      </div>

      <div className="evidence-snippet">
        {hasExpandableContent ? (
          <>
            {hasLongSnippet ? truncate(evidence.snippet, MAX_SNIPPET_LENGTH) : evidence.snippet}
            <details className="snippet-expand">
              <summary>{hasVideoDescription ? '查看完整视频描述' : '查看完整文本'}</summary>
              <p>{hasVideoDescription ? evidence.description_text : evidence.snippet}</p>
            </details>
          </>
        ) : (
          evidence.snippet
        )}
      </div>

      {evidence.thread_summary ? <p className="muted evidence-thread">帖子摘要：{evidence.thread_summary}</p> : null}
      <EvidenceImages evidence={[evidence]} showCitations={false} />
      {evidence.graph_evidence?.length ? (
        <details>
          <summary>查看图谱关系与原文</summary>
          {evidence.graph_evidence.map((entry) => (
            <div key={entry.id}>
              <p>{entry.subject} · {entry.predicate} · {entry.object}</p>
              <p>原文陈述：{{ asserted: '肯定', uncertain: '不确定', negated: '否定' }[entry.assertion]}（未独立事实验证）</p>
              {Object.entries(entry.qualifiers).map(([key, value]) => <p key={key}>{key}：{value}</p>)}
              <p>{entry.quote}</p>
            </div>
          ))}
        </details>
      ) : null}
      {evidence.forum_graph_evidence?.length ? (
        <details>
          <summary>查看论坛案例与验证反馈</summary>
          {evidence.forum_graph_evidence.map((entry) => {
            const href = safeExternalHref(entry.post_url);
            const outcome = ({ reported_success: '作者报告成功（仅该案例）', reported_failure: '作者报告无效', mixed: '结果不一致', unknown: '结果未知' } as Record<string, string>)[entry.outcome || ''] || '尚无明确验证反馈';
            return (
              <div key={entry.id}>
                {entry.role === 'context' ? <p>案例背景（操作建议另见对应楼层）</p> : <p>{entry.subject} · {entry.predicate} · {entry.object || entry.value}</p>}
                <p>{entry.role === 'feedback' ? '验证反馈' : entry.role === 'context' ? '问题与上下文' : FORUM_ACT_LABELS.get(entry.act) || '技术陈述'} · {entry.author} · 第 {entry.post_number} 楼</p>
                <p>{outcome}；原文陈述不等于通用事实验证。</p>
                {entry.accepted_answer ? <p>社区已采纳；有效范围仍以具体条件和反馈为准。</p> : null}
                {entry.combined_operations ? <p>多项操作的联合结果，不能分别认定每项都有效。</p> : null}
                {forumConditions(entry.qualifiers).map(([label, value]) => <p key={`${label}:${value}`}>{label}：{value}</p>)}
                <blockquote>{entry.quote}</blockquote>
                {href ? <a href={href} target="_blank" rel="noreferrer">查看该楼层原文</a> : null}
              </div>
            );
          })}
        </details>
      ) : null}

      {youtubeEmbedHref ? (
        <div className="video-wrapper">
          <iframe
            className="video-player"
            src={youtubeEmbedHref}
            title={evidence.title || 'YouTube 视频'}
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
            allowFullScreen
            referrerPolicy="strict-origin-when-cross-origin"
            data-testid="youtube-video-evidence"
          />
        </div>
      ) : videoHref ? (
        <div className="video-wrapper">
          <video controls preload="metadata" src={videoHref} data-testid="video-evidence" className="video-player">
            当前浏览器不支持视频播放。
          </video>
        </div>
      ) : null}

      {canonicalHref ? (
        <a href={canonicalHref} target="_blank" rel="noreferrer" className="evidence-link">
          {evidence.canonical_url}
        </a>
      ) : (
        <span className="muted">{evidence.canonical_url}</span>
      )}
    </article>
  );
}
