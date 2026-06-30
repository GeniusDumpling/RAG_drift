const ITEM_TYPE_LABELS: Record<string, string> = {
  doc_page: '文档',
  thread: '帖子',
  post: '评论',
  article: '文章',
  comment: '回复',
  video_description: '视频描述',
};

export function itemTypeLabel(itemType: string): string {
  return ITEM_TYPE_LABELS[itemType] ?? itemType;
}

export function safeExternalHref(url: string | null | undefined): string | null {
  const trimmed = url?.trim();
  if (!trimmed) {
    return null;
  }

  try {
    const parsed = new URL(trimmed);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? trimmed : null;
  } catch {
    return null;
  }
}
