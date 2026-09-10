import { ChangeEvent, useEffect, useMemo, useState } from 'react';

import { getContent, listContents } from '../api/client';
import { itemTypeLabel } from '../utils/links';
import type { ContentDetail, ContentListItem, Page } from '../api/types';
import { formatErrorMessage } from '../utils/errors';
import { safeExternalHref } from '../utils/links';

const EMPTY_STATE_COPY = '暂无数据。请先启动后端并运行 demo seed 脚本。';
const CONTENT_UNAVAILABLE_COPY = 'Content 详情暂不可用：API 请求失败。';
const LOADING_CONTENT_COPY = '正在加载 Content 数据...';
const LOADING_CONTENT_DETAIL_COPY = '正在加载 Content 详情...';
const CONTENT_VIEW_OPTIONS = [
  { value: 'post', label: '帖子类数据' },
  { value: 'video_description', label: '视频描述类数据' },
] as const;

type ContentViewType = (typeof CONTENT_VIEW_OPTIONS)[number]['value'];

type ContentDetailPageProps = {
  selectedContentId?: string;
  onOpenRun?: (runId: string) => void;
};

async function listContentsForView(contentViewType: ContentViewType): Promise<Page<ContentListItem>> {
  if (contentViewType === 'video_description') {
    return listContents({ limit: 100, item_type: contentViewType });
  }

  const [threadsPage, postsPage, commentsPage] = await Promise.all([
    listContents({ limit: 100, item_type: 'thread' }),
    listContents({ limit: 100, item_type: 'post' }),
    listContents({ limit: 100, item_type: 'comment' }),
  ]);
  return {
    items: [...threadsPage.items, ...postsPage.items, ...commentsPage.items].sort(
      (left, right) => Date.parse(right.fetched_at) - Date.parse(left.fetched_at),
    ),
    total: threadsPage.total + postsPage.total + commentsPage.total,
    limit: threadsPage.limit + postsPage.limit + commentsPage.limit,
    offset: 0,
  };
}

export function ContentDetailPage({ selectedContentId: externallySelectedContentId = '', onOpenRun }: ContentDetailPageProps = {}) {
  const [contents, setContents] = useState<Page<ContentListItem>>();
  const [activeContentId, setActiveContentId] = useState(externallySelectedContentId);
  const [contentViewType, setContentViewType] = useState<ContentViewType>('post');
  const [detail, setDetail] = useState<ContentDetail>();
  const [listError, setListError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [listLoading, setListLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    let ignore = false;
    setListError('');
    setListLoading(true);
    listContentsForView(contentViewType)
      .then((page) => {
        if (!ignore) {
          setContents(page);
          setActiveContentId((current) => current || externallySelectedContentId || page.items[0]?.id || '');
        }
      })
      .catch((caught) => {
        if (!ignore) {
          setContents(undefined);
          setListError(formatErrorMessage('无法加载 Content 列表', caught));
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
  }, [contentViewType, externallySelectedContentId]);

  useEffect(() => {
    if (externallySelectedContentId) {
      setActiveContentId(externallySelectedContentId);
    }
  }, [externallySelectedContentId]);

  useEffect(() => {
    setDetailError('');
    if (!activeContentId) {
      setDetail(undefined);
      setDetailLoading(false);
      return;
    }
    let ignore = false;
    setDetail(undefined);
    setDetailLoading(true);
    getContent(activeContentId)
      .then((content) => {
        if (!ignore) {
          setDetail(content);
        }
      })
      .catch((caught) => {
        if (!ignore) {
          setDetail(undefined);
          setDetailError(formatErrorMessage('无法加载 Content 详情', caught));
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
  }, [activeContentId]);

  const displayedDetail = detail?.id === activeContentId ? detail : undefined;
  const indexedTextPreview = useMemo(() => {
    if (!displayedDetail) {
      return '';
    }
    return displayedDetail.chunks.map((chunk) => chunk.display_text).join('\n\n') || displayedDetail.raw_page.raw_text || '';
  }, [displayedDetail]);
  const rawPageUrl = displayedDetail?.raw_page.final_url || displayedDetail?.raw_page.requested_url || '';
  const rawPageHref = safeExternalHref(rawPageUrl);
  const contentOptions = useMemo(() => {
    const loadedContents = contents?.items ?? [];
    if (!loadedContents.length) {
      return [];
    }

    const options = loadedContents.map((item) => ({
      id: item.id,
      label: `${itemTypeLabel(item.item_type)} · ${item.title || item.canonical_url}`,
    }));
    if (activeContentId && !loadedContents.some((item) => item.id === activeContentId)) {
      return [
        {
          id: activeContentId,
          label: `${itemTypeLabel(displayedDetail?.item_type ?? '已选择')} · ${displayedDetail?.title || displayedDetail?.canonical_url || activeContentId} · ${activeContentId}`,
        },
        ...options,
      ];
    }
    return options;
  }, [activeContentId, contents, displayedDetail]);

  function handleContentChange(event: ChangeEvent<HTMLSelectElement | HTMLInputElement>) {
    setActiveContentId(event.target.value);
  }

  function handleContentViewTypeChange(event: ChangeEvent<HTMLSelectElement>) {
    setContentViewType(event.target.value as ContentViewType);
    setActiveContentId('');
  }

  const contentLoadFailed = Boolean(listError || detailError);
  const detailSelectionPending = Boolean(activeContentId && !displayedDetail && !detailError);
  const loading = listLoading || detailLoading || detailSelectionPending;
  const loadingCopy = detailLoading || detailSelectionPending ? LOADING_CONTENT_DETAIL_COPY : LOADING_CONTENT_COPY;

  return (
    <section>
      <div className="page-title">
        <p className="eyebrow">语料库</p>
        <h1>内容详情</h1>
        <p className="muted">选择查看帖子类或视频描述记录，并展示其原始快照、分块和提取元数据。</p>
      </div>

      <div className="card controls-card">
        <label htmlFor="content-view-type">查看数据类型</label>
        <select id="content-view-type" value={contentViewType} onChange={handleContentViewTypeChange}>
          {CONTENT_VIEW_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <label htmlFor="content-selector">内容</label>
        {contentOptions.length ? (
          <select id="content-selector" value={activeContentId} onChange={handleContentChange}>
            {contentOptions.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        ) : (
          <input
            id="content-selector"
            value={activeContentId}
            onChange={handleContentChange}
            placeholder="粘贴 content item id"
          />
        )}
      </div>

      {listError ? (
        <p className="card error-text" role="alert">
          {listError}
        </p>
      ) : null}
      {detailError ? (
        <p className="card error-text" role="alert">
          {detailError}
        </p>
      ) : null}
      {loading ? (
        <p className="card status-line" role="status" aria-live="polite">
          {loadingCopy}
        </p>
      ) : null}

      {displayedDetail ? (
        <>
          <section className="card">
            <div className="card-header">
              <div>
                <h2>{displayedDetail.title || '未命名内容'}</h2>
                <p className="muted compact">{displayedDetail.canonical_url}</p>
              </div>
              <span className="badge">{itemTypeLabel(displayedDetail.item_type)}</span>
            </div>
            <dl className="kv-grid">
              <div>
                <dt>来源</dt>
                <dd>{displayedDetail.source.name}</dd>
              </div>
              <div>
                <dt>作者</dt>
                <dd>{displayedDetail.author_name || '未知'}</dd>
              </div>
              <div>
                <dt>抓取时间</dt>
                <dd>{new Date(displayedDetail.fetched_at).toLocaleString()}</dd>
              </div>
              <div>
                <dt>发布时间</dt>
                <dd>{displayedDetail.published_at ? new Date(displayedDetail.published_at).toLocaleString() : 'n/a'}</dd>
              </div>
              <div>
                <dt>提取置信度</dt>
                <dd>{displayedDetail.extraction_confidence ?? displayedDetail.raw_page.extraction_confidence ?? 'n/a'}</dd>
              </div>
              <div>
                <dt>解析状态</dt>
                <dd>{displayedDetail.raw_page.parse_status}</dd>
              </div>
            </dl>
            <div className="tag-row">
              {displayedDetail.tags.length
                ? displayedDetail.tags.map((tag) => <span className="badge" key={tag}>{tag}</span>)
                : '无标签'}
            </div>
            <p>{displayedDetail.summary_text || '暂无摘要。'}</p>
            <div className="button-row link-row">
              {rawPageHref ? (
                <a href={rawPageHref} target="_blank" rel="noreferrer">
                  Raw page 链接
                </a>
              ) : (
                <span className="muted" aria-disabled="true">
                  Raw page 链接不可用：{rawPageUrl || 'n/a'}
                </span>
              )}
              {onOpenRun ? (
                <button className="link-button" type="button" onClick={() => onOpenRun(displayedDetail.crawl_run.id)}>
                  Run 链接
                </button>
              ) : (
                <span className="muted" aria-disabled="true">
                  Run 链接不可用
                </span>
              )}
            </div>
          </section>

          <section className="card">
            <h2>索引文本预览</h2>
            {indexedTextPreview ? <pre>{indexedTextPreview}</pre> : <p className="muted">{EMPTY_STATE_COPY}</p>}
          </section>

          <section className="card">
            <h2>分块</h2>
            {displayedDetail.chunks.length ? (
              <ol className="chunk-list">
                {displayedDetail.chunks.map((chunk) => (
                  <li key={chunk.id}>
                    <div className="card-header compact-header">
                      <strong>Chunk {chunk.chunk_index}</strong>
                      <span className="badge">{chunk.embed_status}</span>
                    </div>
                    <p>{chunk.display_text}</p>
                    <dl className="kv-grid">
                      <div>
                        <dt>字符范围</dt>
                        <dd>
                          {chunk.char_start ?? 'n/a'}–{chunk.char_end ?? 'n/a'}
                        </dd>
                      </div>
                      <div>
                        <dt>词元数</dt>
                        <dd>{chunk.token_count ?? 'n/a'}</dd>
                      </div>
                      <div>
                        <dt>Vector backend</dt>
                        <dd>{chunk.vector_backend || 'n/a'}</dd>
                      </div>
                      <div>
                        <dt>Vector point</dt>
                        <dd>{chunk.vector_point_id || chunk.qdrant_point_id || 'n/a'}</dd>
                      </div>
                    </dl>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="muted">{EMPTY_STATE_COPY}</p>
            )}
          </section>
        </>
      ) : loading ? null : contentLoadFailed ? (
        <p className="card muted">{CONTENT_UNAVAILABLE_COPY}</p>
      ) : (
        <p className="card empty-state">{EMPTY_STATE_COPY}</p>
      )}
    </section>
  );
}
