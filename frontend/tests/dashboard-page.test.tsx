import '@testing-library/jest-dom/vitest';
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DashboardPage } from '../src/pages/DashboardPage';

const EMPTY_STATE_COPY = '暂无数据。请先启动后端并运行 demo seed 脚本。';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('DashboardPage', () => {
  it('renders an explicit API error instead of empty-state copy when dashboard requests fail', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('backend offline')));

    render(<DashboardPage />);

    expect(await screen.findByRole('alert')).toHaveTextContent('无法加载总览数据: backend offline');
    expect(screen.queryByText(EMPTY_STATE_COPY)).not.toBeInTheDocument();
  });
});
