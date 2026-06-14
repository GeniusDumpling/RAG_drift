import '@testing-library/jest-dom/vitest';
import { render, screen } from '@testing-library/react';
import { SearchPage } from '../src/pages/SearchPage';

it('renders search controls and evidence area', () => {
  render(<SearchPage />);
  expect(screen.getByLabelText('Query')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Search' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Answer' })).toBeInTheDocument();
  expect(screen.getByText('Evidence')).toBeInTheDocument();
});
