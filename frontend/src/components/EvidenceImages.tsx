import { useState } from 'react';
import type { EvidenceObject } from '../api/types';
import { safeExternalHref } from '../utils/links';

export function EvidenceImages({ evidence, showCitations = true, citedIndices }: { evidence: EvidenceObject[]; showCitations?: boolean; citedIndices?: number[] }) {
  const [expanded, setExpanded] = useState(false);
  const apiBase = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '');
  const seen = new Set<string>();
  const entries = evidence.flatMap((item, citation) => (item.images || []).flatMap((image) => {
    if (citedIndices && !citedIndices.includes(citation + 1)) return [];
    // Assets come from our read-only content-addressed API; reject arbitrary image URLs.
    if (!/^[a-f0-9]{64}$/.test(image.asset_id)
      || image.url !== `/ifixit-media/assets/${image.asset_id}` || seen.has(image.asset_id)) return [];
    seen.add(image.asset_id);
    return [{ image, citation: citation + 1 }];
  }));
  if (!entries.length) return null;
  return <div className="evidence-images" aria-label="证据配图">
    {(expanded ? entries : entries.slice(0, 4)).map(({ image, citation }) => (
      <figure key={image.asset_id}>
        <a href={`${apiBase}${image.url}`} target="_blank" rel="noreferrer" aria-label={`查看原图：${image.caption}`}>
          <img src={`${apiBase}${image.url}`} alt={image.caption} loading="lazy" width={image.width} height={image.height} />
        </a>
        <figcaption>
          <span>{showCitations ? `[${citation}] ` : ''}{image.step_order ? `步骤 ${image.step_order} · ` : ''}{image.caption}</span>
          <a href={safeExternalHref(image.guide_url) || undefined} target="_blank" rel="noreferrer">原始指南</a>
        </figcaption>
        {image.observation ? <details><summary>图片说明与可见文字</summary>
          <p>{image.observation}</p>
          {image.ocr_text?.length ? <p>模型识别文字：{image.ocr_text.join('；')}</p> : null}
          <small>图片说明由模型提取，模糊丝印请结合原图和指南核对。</small>
        </details> : null}
      </figure>
    ))}
    {entries.length > 4 ? <button type="button" onClick={() => setExpanded(!expanded)}>
      {expanded ? '收起配图' : `查看全部 ${entries.length} 张配图`}
    </button> : null}
  </div>;
}
