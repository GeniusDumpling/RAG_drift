import type { EvidenceObject } from '../api/types';
import { itemTypeLabel, safeExternalHref } from '../utils/links';

const MAX_SNIPPET_LENGTH = 300;

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
        <span className="badge">{evidence.matched_by}</span>
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
