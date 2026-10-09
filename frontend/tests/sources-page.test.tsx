import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { SourcesPage } from '../src/pages/SourcesPage';

const task = { id: 'video-collect', name: '视频采集', timer: 'rag-video-collect.timer',
  scope: 'user', schedule: '每两小时', timer_state: 'active', enabled: 'enabled',
  next_run: '明天', last_run: '今天', service_state: 'inactive', last_result: 'success',
  exit_code: '0', available: true };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it('lists system timers and posts the selected task once', async () => {
  const fetch = vi.fn((_url: unknown, init?: RequestInit) => Promise.resolve(new Response(
    JSON.stringify(init?.method === 'POST' ? { status: 'accepted' } : { items: [task] }),
    { status: 200 })));
  vi.stubGlobal('fetch', fetch);
  render(<SourcesPage />);
  await screen.findByText('rag-video-collect.timer');
  fireEvent.change(screen.getByLabelText('采集操作口令'), { target: { value: 'test-token' } });
  fireEvent.click(screen.getByRole('button', { name: '手动采集 视频采集' }));
  expect(await screen.findByText(/已提交 视频采集/)).toBeInTheDocument();
  expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
  expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/scheduled-tasks/video-collect/trigger'),
    expect.objectContaining({ method: 'POST', headers: { Authorization: 'Bearer test-token' } }));
});
it('disables active jobs', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [{ ...task, service_state: 'activating' }] }))));
  render(<SourcesPage />);
  const button = await screen.findByRole('button', { name: '手动采集 视频采集' });
  fireEvent.change(screen.getByLabelText('采集操作口令'), { target: { value: 'test-token' } });
  expect(button).toBeDisabled();
  expect(button).toHaveTextContent('采集中');
});
it('shows bridge errors instead of fake tasks', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('bridge unavailable')));
  render(<SourcesPage />);
  expect(await screen.findByRole('alert')).toHaveTextContent('bridge unavailable');
});
