import '@testing-library/jest-dom/vitest';
import { render, screen } from '@testing-library/react';
import { RunDetailPage } from '../src/pages/RunDetailPage';

it('renders run counters and event timeline headings', () => {
  render(<RunDetailPage />);
  expect(screen.getByText('Run Detail')).toBeInTheDocument();
  expect(screen.getByText('Stage Counters')).toBeInTheDocument();
  expect(screen.getByText('Event Timeline')).toBeInTheDocument();
});
