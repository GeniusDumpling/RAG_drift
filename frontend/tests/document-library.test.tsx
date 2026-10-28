import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DocumentLibraryPanel } from '../src/pages/DocumentLibraryPanel';
import { ContentDetailPage } from '../src/pages/ContentDetailPage';

const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
const model = { slug: 'air-2s', name: 'DJI Air 2S', manufacturer: 'DJI' };
const processing = { id: 'p1', status: 'ready', chunker_version: 'v5', language_policy: 'zh-Hans', created_at: '2026-10-27T12:00:00Z' };
const summary = { id: 'doc1', title: 'Air 2S 用户手册', document_type: 'user_manual', models: [model], status: 'ready' };
const detail = { document: { ...summary, source_url: 'https://example.com/manual.pdf', active_processing_id: 'p1', metadata_json: {} },
  processing, processings: [processing, { ...processing, id: 'p0', chunker_version: 'v4' }], models: [model],
  stats: { pages: 55, chinese_pages: 55, elements: 300, chunks: 142, indexed_chunks: 142, tables: 4, figures: 86, quarantined_figures: 12 } };
const page = (items: unknown[], total = items.length, limit = 30, offset = 0) => ({ items, total, limit, offset });

function mockApi() {
  const fetch = vi.fn((input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname === '/contents') return Promise.resolve(json(page([])));
    if (url.pathname === '/document-library/models') return Promise.resolve(json({ items: [model] }));
    if (url.pathname === '/document-library') return Promise.resolve(json(page([summary], 51, 50, Number(url.searchParams.get('offset') || 0))));
    if (url.pathname === '/document-library/doc1') {
      const old = url.searchParams.get('processing_id') === 'p0';
      return Promise.resolve(json({ ...detail, processing: { ...processing, id: old ? 'p0' : 'p1' } }));
    }
    if (url.pathname === '/document-library/doc1/chunks') return Promise.resolve(json(page([{ id: 'chunk1', chunk_index: 0, chunk_type: 'text', display_text: url.searchParams.get('processing_id') === 'p0' ? '旧版检索文本' : '新版检索文本', embed_status: 'success', metadata_json: { source_pages: [5] } }])));
    if (url.pathname === '/document-library/doc1/pages') return Promise.resolve(json(page([{ id: 'page5', source_page_no: 5, included: true, language_state: 'zh', render_asset_id: 'page-image', metadata_json: {} }])));
    if (url.pathname === '/document-library/doc1/elements') {
      const kind = url.searchParams.get('kind');
      if (kind === 'table') return Promise.resolve(json(page([{ id: 'table1', element_type: 'table', table_json: { rows: [['灯态', '含义'], ['红灯常亮', '严重错误']] }, metadata_json: {}, regions: [{ source_page_no: 5 }] }])));
      if (kind === 'figure') return Promise.resolve(json(page([{ id: 'fig1', element_type: 'figure', asset_id: 'fig-image', metadata_json: { description: { description_zh: '遥控器按钮图' }, quality_gate: { status: 'quarantined' } } }])));
      if (kind === 'footnote') return Promise.resolve(json(page([{ id: 'fn1', element_type: 'footnote', text: '无风环境下测得。', metadata_json: {} }])));
      return Promise.resolve(json(page([])));
    }
    return Promise.reject(new Error(`Unexpected URL: ${url}`));
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('processed document browser', () => {
  it('opens from content details and displays tables, image quality and source pages', async () => {
    mockApi();
    render(<ContentDetailPage />);
    fireEvent.change(screen.getByLabelText('查看数据类型'), { target: { value: 'document' } });
    await screen.findByText('新版检索文本');
    fireEvent.click(screen.getByRole('tab', { name: '表格' }));
    expect(await screen.findByText('严重错误')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '原文第 5 页' }));
    expect(await screen.findByAltText('原文第 5 页')).toHaveAttribute('src', expect.stringContaining('/document-library/assets/page-image'));
    fireEvent.click(screen.getByRole('tab', { name: '图片与说明' }));
    expect(await screen.findByText('遥控器按钮图')).toBeInTheDocument();
    expect(screen.getByText(/该图片说明未通过质量检查/)).toBeInTheDocument();
    expect(screen.getByAltText('图片')).toHaveAttribute('src', expect.stringContaining('/document-library/assets/fig-image'));
  });

  it('uses the selected processing version and clears text from the previous version', async () => {
    const fetch = mockApi();
    render(<DocumentLibraryPanel />);
    await screen.findByText('新版检索文本');
    fireEvent.change(screen.getByLabelText('文档处理版本'), { target: { value: 'p0' } });
    expect(await screen.findByText('旧版检索文本')).toBeInTheDocument();
    expect(screen.queryByText('新版检索文本')).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('processing_id=p0'), undefined);
    expect(screen.getByText('未发布或历史版本')).toBeInTheDocument();
  });

  it('filters by model and query, paginates documents, and requests footnotes separately', async () => {
    const fetch = mockApi();
    render(<DocumentLibraryPanel />);
    await screen.findByText('新版检索文本');
    fireEvent.change(screen.getByLabelText('文档关联型号'), { target: { value: 'air-2s' } });
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining('model=air-2s'), undefined));
    fireEvent.change(screen.getByLabelText('文档搜索'), { target: { value: '手册' } });
    fireEvent.click(screen.getByRole('button', { name: '搜索文档' }));
    await screen.findByText('新版检索文本');
    const list = screen.getByRole('complementary', { name: '文档列表' });
    fireEvent.click(within(list).getByRole('button', { name: '下一页' }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringMatching(/offset=50.*model=air-2s/), undefined));
    await screen.findByText('新版检索文本');
    fireEvent.click(screen.getByRole('tab', { name: '参数与脚注' }));
    fireEvent.change(screen.getByLabelText('文档元素类型'), { target: { value: 'footnote' } });
    expect(await screen.findByText('无风环境下测得。')).toBeInTheDocument();
  });

  it('shows explicit errors when the document database is unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    render(<DocumentLibraryPanel />);
    expect(await screen.findByText('无法加载处理后文档: offline')).toBeInTheDocument();
    expect(screen.queryByText('新版检索文本')).not.toBeInTheDocument();
  });
});
