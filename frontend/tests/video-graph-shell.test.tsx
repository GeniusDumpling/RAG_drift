import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { App } from '../src/App';

vi.mock('../src/pages/SupplierPage', () => ({ SupplierPage: () => <div>供应链内容区域</div> }));
vi.mock('../src/pages/AiChatPage', () => ({ AiChatPage: () => <div>AI 问答内容区域</div> }));
vi.mock('../src/components/video-graph/InteractiveGraphCanvas', () => ({
  InteractiveGraphCanvas: () => <div aria-label="交互画布测试替身" />,
}));
afterEach(() => {
  cleanup(); vi.unstubAllGlobals(); window.history.replaceState({}, '', '/');
});

it('shows graph, supply chain and AI chat in the same application shell', async () => {
  window.history.replaceState({}, '', '/?view=VideoGraph');
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => Promise.resolve(new Response(JSON.stringify(
    String(input).includes('/overview')
      ? { videos: 1, active_videos: 1, entities: 1, relations: 0, evidence: 0, builds: {} }
      : { nodes: [{ id: 'px4', label: 'PX4', kind: 'entity', entity_type: 'software' }], edges: [], truncated: false },
  ), { status: 200, headers: { 'Content-Type': 'application/json' } }))));
  const { container } = render(<App />);
  expect(await screen.findByLabelText('交互画布测试替身')).toBeInTheDocument();
  expect(screen.getByRole('navigation', { name: '主导航 Primary' })).toBeVisible();
  expect(container.querySelector('.app-topbar')).not.toBeInTheDocument();
  expect(container.querySelector('.main')).toHaveClass('main--graph');
  expect(screen.queryByRole('heading', { name: '视频知识图谱', level: 1 })).not.toBeInTheDocument();
  expect(screen.queryByText('视频情报')).not.toBeInTheDocument();
  expect(screen.queryByRole('navigation', { name: '图谱顶部导航' })).not.toBeInTheDocument();
  expect(document.body).not.toHaveClass('graph-workspace-active');
  fireEvent.click(screen.getByRole('button', { name: '供应链', exact: true }));
  expect(screen.getByText('供应链内容区域')).toBeInTheDocument();
  expect(container.querySelector('.app-topbar')).toHaveTextContent('供应链');
  expect(container.querySelector('.main')).not.toHaveClass('main--graph');
  fireEvent.click(screen.getByRole('button', { name: 'AI 问答', exact: true }));
  expect(screen.getByText('AI 问答内容区域')).toBeInTheDocument();
  expect(container.querySelector('.app-topbar')).toHaveTextContent('AI 问答');
  fireEvent.click(screen.getByRole('button', { name: '视频知识图谱', exact: true }));
  expect(await screen.findByLabelText('交互画布测试替身')).toBeInTheDocument();
  expect(screen.getByRole('navigation', { name: '主导航 Primary' })).toBeVisible();
});
