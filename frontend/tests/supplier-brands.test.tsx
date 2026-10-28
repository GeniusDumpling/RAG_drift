import '@testing-library/jest-dom/vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SupplierPage } from '../src/pages/SupplierPage';

const names: Record<string, string> = { '大疆': '欧菲光', Parrot: 'Ambarella', Skydio: 'Nidec' };
const totals: Record<string, number> = { '大疆': 74, Parrot: 6, Skydio: 3 };
const brands = Object.entries(totals).map(([buyer_name, total_suppliers]) => ({ buyer_name, total_suppliers }));
const relation = (buyer: string) => ({
  id: buyer, buyer_name: buyer, supplier_name: names[buyer], supply_content: '相机/处理器/电机',
  credibility: '明确', latest_verification_confidence: '明确', seen_count: 1,
  source_urls: [], verify_status: '已验证', drone_models: [buyer === 'Parrot' ? 'ANAFI USA' : 'X10'],
  evidence_years: buyer === 'Skydio' ? [] : [2024],
});
const page = (buyers: string[], total = buyers.length) => ({ items: buyers.map(relation), total, limit: 50, offset: 0 });
const overview = (total: number) => ({ total_suppliers: total, confirmed_relations: total,
  verified: 2, unverified: Math.max(0, total - 2), verify_failed: 0 });
const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
function setup(override?: (url: URL) => Promise<Response> | undefined) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = new URL(String(input), 'http://localhost');
    const custom = override?.(url);
    if (custom) return custom;
    const buyer = url.searchParams.get('buyer_name');
    if (url.pathname === '/supplier/brands') return Promise.resolve(json(brands));
    if (url.pathname === '/supplier/overview') return Promise.resolve(json(overview(buyer ? totals[buyer] : 83)));
    if (url.pathname === '/supplier/relations') return Promise.resolve(json(page(buyer ? [buyer] : Object.keys(names), buyer ? totals[buyer] : 83)));
    return Promise.resolve(json([]));
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<SupplierPage />);
  return fetchMock;
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('SupplierPage brand scope', () => {
  it('resets search, pagination and expanded evidence on a brand change', async () => {
    let resolveEvidence!: (r: Response) => void;
    const pendingEvidence = new Promise<Response>((resolve) => { resolveEvidence = resolve; });
    const fetchMock = setup((url) => url.pathname.endsWith('/大疆/verifications') ? pendingEvidence : undefined);
    await screen.findByText('欧菲光');
    const search = screen.getByRole('textbox', { name: '搜索供应商或模块' });
    fireEvent.change(search, { target: { value: '相机' } });
    fireEvent.click(screen.getByRole('button', { name: '搜索' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '下一页' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    await screen.findByText('欧菲光');
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).includes('offset=50'))).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: '展开欧菲光的验证记录' }));
    await screen.findByRole('region', { name: '欧菲光的验证记录' });
    fireEvent.change(screen.getByLabelText('品牌选择'), { target: { value: 'Parrot' } });
    expect(screen.queryByText('欧菲光')).not.toBeInTheDocument();
    expect(search).toHaveValue('');
    await screen.findByText('Ambarella');
    expect(screen.getByText('机型：ANAFI USA')).toBeInTheDocument();
    expect(screen.getByText('证据年份：2024')).toBeInTheDocument();
    expect(screen.getByLabelText('品牌统计')).toHaveTextContent('Parrot · 已收录 6 家供应商');
    const requests = fetchMock.mock.calls.map(([input]) => new URL(String(input), 'http://localhost'));
    const last = requests.filter((url) => url.pathname === '/supplier/relations').at(-1)!;
    expect(last.searchParams.get('buyer_name')).toBe('Parrot');
    expect(last.searchParams.get('offset')).toBe('0');
    expect(last.searchParams.has('q')).toBe(false);
    await act(async () => resolveEvidence(json([{ id: 'old', evidence_md: '过期的验证证据' }])));
    expect(screen.queryByText('过期的验证证据')).not.toBeInTheDocument();
  });

  it('ignores late list/statistic responses and removes the filter for all brands', async () => {
    let resolveList!: (r: Response) => void;
    let resolveOverview!: (r: Response) => void;
    const list = new Promise<Response>((resolve) => { resolveList = resolve; });
    const summary = new Promise<Response>((resolve) => { resolveOverview = resolve; });
    const fetchMock = setup((url) => {
      if (url.searchParams.get('buyer_name') !== 'Parrot') return;
      if (url.pathname === '/supplier/relations') return list;
      if (url.pathname === '/supplier/overview') return summary;
    });
    await screen.findByRole('option', { name: 'Skydio（3）' });
    fireEvent.change(screen.getByLabelText('品牌选择'), { target: { value: 'Parrot' } });
    fireEvent.change(screen.getByLabelText('品牌选择'), { target: { value: 'Skydio' } });
    await screen.findByText('Nidec');
    expect(screen.getByText('证据年份：未注明')).toBeInTheDocument();
    await act(async () => { resolveList(json(page(['Parrot']))); resolveOverview(json(overview(6))); });
    expect(screen.queryByText('Ambarella')).not.toBeInTheDocument();
    expect(screen.getByLabelText('品牌统计')).toHaveTextContent('Skydio · 已收录 3 家供应商');
    fireEvent.change(screen.getByLabelText('品牌选择'), { target: { value: '' } });
    await screen.findByText('Ambarella');
    expect(screen.getByText('Nidec')).toBeInTheDocument();
    const lastRequests = fetchMock.mock.calls.map(([input]) => new URL(String(input), 'http://localhost')).slice(-2);
    expect(lastRequests.every((url) => !url.searchParams.has('buyer_name'))).toBe(true);
  });
});
