import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { SearchPage } from '../src/pages/SearchPage';

describe('Literature research mode', () => {
  it('switches from RAG controls to the asynchronous IEEE research form', () => {
    render(<SearchPage initialQuery="无人机 避障" />);

    fireEvent.click(screen.getByRole('button', { name: 'IEEE 文献研究' }));

    expect(screen.getByLabelText('无人机关键词')).toHaveValue('无人机 避障');
    expect(screen.getByLabelText('研究方向数')).toHaveValue(5);
    expect(screen.getByRole('button', { name: '开始文献研究' })).toBeInTheDocument();
    expect(screen.getByLabelText('文献任务 ID')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '检索 Search' })).not.toBeInTheDocument();
  });
});
