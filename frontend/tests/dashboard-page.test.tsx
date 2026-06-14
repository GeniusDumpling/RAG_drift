import '@testing-library/jest-dom/vitest';
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DashboardPage } from '../src/pages/DashboardPage';

const EMPTY_STATE_COPY = 'No data loaded yet. Start the backend and run the demo seed script.';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('DashboardPage', () => {
  it('renders an explicit API error instead of empty-state copy when dashboard requests fail', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('backend offline')));

    render(<DashboardPage />);

    expect(await screen.findByRole('alert')).toHaveTextContent('Unable to load dashboard data: backend offline');
    expect(screen.queryByText(EMPTY_STATE_COPY)).not.toBeInTheDocument();
  });
});
