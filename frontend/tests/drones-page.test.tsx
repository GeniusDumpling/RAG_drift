import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import App from '../src/App';
import type { DroneDetail, DroneSummary } from '../src/api/types';
import { DronesPage } from '../src/pages/DronesPage';

const mavic: DroneSummary = {
  slug: 'mavic-4-pro', name: 'DJI Mavic 4 Pro', manufacturer: 'DJI', category: '消费航拍',
  status: 'saved', group_count: 2, parameter_count: 2, note: '',
  collected_at: '2026-10-22T15:56:36+08:00', source_url: 'https://www.dji.com/cn/support/product/mavic-4-pro',
};
const inspire: DroneSummary = {
  ...mavic, slug: 'inspire-1-pro-and-raw', name: '悟 Inspire 1 Pro/RAW', category: '专业影视',
  status: 'partial', group_count: 1, parameter_count: 1,
  note: '仅包含 Inspire 1 Pro 官方旧版参数；RAW 版本参数未补齐。',
};
const mavicDetail: DroneDetail = { ...mavic, groups: [
  { name: '飞行器', parameters: [{ name: '最长飞行时间', value: '51 分钟\n无风环境测得。' }] },
  { name: '电池', parameters: [{ name: '容量', value: '6654 毫安时' }] },
] };
const inspireDetail: DroneDetail = { ...inspire, groups: [
  { name: '飞行器', parameters: [{ name: '型号', value: 'T600' }] },
] };
const catalog = { items: [mavic, inspire], total: 2, parameter_count: 3, partial_count: 1 };

function json(body: unknown) { return new Response(JSON.stringify(body), { status: 200 }); }
function mockCatalog() {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://localhost').pathname;
    if (url === '/drones') return Promise.resolve(json(catalog));
    if (url === '/drones/mavic-4-pro') return Promise.resolve(json(mavicDetail));
    if (url === '/drones/inspire-1-pro-and-raw') return Promise.resolve(json(inspireDetail));
    if (String(input).includes('?')) return Promise.resolve(json({ items: [], total: 0, limit: 50, offset: 0 }));
    return Promise.reject(new Error(`Unexpected URL: ${url}`));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('DronesPage', () => {
  it('opens from the workspace and displays local model parameters and source', async () => {
    mockCatalog();
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: '无人机信息' }));
    expect(await screen.findByRole('heading', { name: 'DJI Mavic 4 Pro' })).toBeInTheDocument();
    expect(screen.getByText(/无风环境测得/)).toHaveTextContent('51 分钟');
    expect(screen.getByRole('link', { name: '查看官方来源 ↗' })).toHaveAttribute('href', mavic.source_url);
    expect(screen.getByRole('button', { name: '无人机信息' })).toHaveAttribute('aria-current', 'page');
  });

  it('filters models and displays the partial-collection note for the selected model', async () => {
    mockCatalog();
    render(<DronesPage />);
    await screen.findByText('6654 毫安时');
    fireEvent.change(screen.getByLabelText('无人机类型'), { target: { value: '专业影视' } });
    expect(await screen.findByText('T600')).toBeInTheDocument();
    expect(screen.getByRole('note')).toHaveTextContent(inspire.note);
    expect(screen.queryByText('6654 毫安时')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('无人机类型'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('搜索无人机型号'), { target: { value: 'mavic4' } });
    expect(await screen.findByText('6654 毫安时')).toBeInTheDocument();
    expect(within(screen.getByRole('complementary', { name: '无人机型号列表' })).queryByText(inspire.name)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('搜索无人机型号'), { target: { value: '不存在的型号' } });
    expect(screen.getByText('未找到匹配型号，请调整搜索或类型。')).toBeInTheDocument();
    expect(screen.queryByText('6654 毫安时')).not.toBeInTheDocument();
  });

  it('searches parameter values and filters parameter groups', async () => {
    mockCatalog();
    render(<DronesPage />);
    await screen.findByText('6654 毫安时');
    fireEvent.change(screen.getByLabelText('搜索技术参数'), { target: { value: '无风' } });
    expect(screen.getByText(/无风环境测得/)).toHaveTextContent('51 分钟');
    expect(screen.queryByText('6654 毫安时')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('搜索技术参数'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('参数分组'), { target: { value: '1' } });
    expect(screen.getByText('6654 毫安时')).toBeInTheDocument();
    expect(screen.queryByText('最长飞行时间')).not.toBeInTheDocument();
  });

  it('clears previous parameters on detail failure and supports retry', async () => {
    const fetchMock = mockCatalog();
    render(<DronesPage />);
    await screen.findByText('6654 毫安时');
    fetchMock.mockImplementation((input) => String(input).includes('inspire')
      ? Promise.reject(new Error('文件暂不可用')) : Promise.resolve(json(catalog)));
    fireEvent.click(screen.getByRole('button', { name: /悟 Inspire 1 Pro\/RAW/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('无法加载技术参数');
    expect(screen.queryByText('6654 毫安时')).not.toBeInTheDocument();
    fetchMock.mockResolvedValue(json(inspireDetail));
    fireEvent.click(screen.getByRole('button', { name: '重试技术参数' }));
    expect(await screen.findByText('T600')).toBeInTheDocument();
  });

  it('ignores a late response after switching models', async () => {
    const fetchMock = mockCatalog();
    let resolveMavic!: (response: Response) => void;
    fetchMock.mockImplementation((input) => {
      if (new URL(String(input), 'http://localhost').pathname === '/drones') return Promise.resolve(json(catalog));
      if (String(input).includes('mavic-4-pro')) return new Promise((resolve) => { resolveMavic = resolve; });
      return Promise.resolve(json(inspireDetail));
    });
    render(<DronesPage />);
    const button = await screen.findByRole('button', { name: /悟 Inspire 1 Pro\/RAW/ });
    await waitFor(() => expect(resolveMavic).toBeTypeOf('function'));
    fireEvent.click(button);
    await screen.findByText('T600');
    resolveMavic(json(mavicDetail));
    await waitFor(() => expect(screen.queryByText('6654 毫安时')).not.toBeInTheDocument());
    expect(screen.getByText('T600')).toBeInTheDocument();
  });

  it('shows catalog errors and an empty catalog explicitly', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error('目录不可用'));
    vi.stubGlobal('fetch', fetchMock);
    render(<DronesPage />);
    expect(await screen.findByRole('alert')).toHaveTextContent('无法加载无人机信息');
    fetchMock.mockResolvedValue(json({ items: [], total: 0, parameter_count: 0, partial_count: 0 }));
    fireEvent.click(screen.getByRole('button', { name: '重新加载' }));
    expect(await screen.findByText('尚未收集无人机型号。')).toBeInTheDocument();
  });
});
