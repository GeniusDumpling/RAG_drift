import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SupplierPage } from '../src/pages/SupplierPage';

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

const relations = {
  items: [
    {
      id: 'relation-1',
      supplier_name: '欧菲光',
      supply_content: '摄像头模组',
      buyer_name: '大疆',
      credibility: '明确',
      latest_verification_confidence: '明确',
      seen_count: 3,
      source_urls: ['https://example.test/source'],
      verify_status: '已验证',
      created_at: '2026-09-09T10:00:00Z',
      updated_at: '2026-09-09T11:00:00Z',
    },
    {
      id: 'relation-2',
      supplier_name: '候选厂商',
      supply_content: '电池',
      buyer_name: '大疆',
      credibility: '疑似',
      latest_verification_confidence: null,
      seen_count: 1,
      source_urls: ['https://example.test/candidate'],
      verify_status: '未验证',
      created_at: '2026-09-09T10:00:00Z',
      updated_at: '2026-09-09T10:00:00Z',
    },
  ],
  total: 2,
  limit: 50,
  offset: 0,
};

const overview = {
  total_suppliers: 2,
  confirmed_relations: 1,
  verified: 1,
  unverified: 1,
  verify_failed: 0,
};

const verifications = [
  {
    id: 'verification-1',
    relation_id: 'relation-1',
    supplier_name: '欧菲光',
    verdict: '确认',
    confidence: '明确',
    supply_content: '摄像头模组',
    evidence_md: '验证结论：确认\n- 主要证据（引用原文，每条约一行，附来源URL）：\n- 欧菲光为大疆供应摄像头模组 https://example.test/primary-evidence',
    evidence_urls: ['https://example.test/verification'],
    orig_source_urls: ['https://example.test/source'],
    verify_time: '2026-09-09T12:00:00Z',
    created_at: '2026-09-09T12:00:00Z',
    updated_at: '2026-09-09T12:00:00Z',
  },
];

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('SupplierPage', () => {
  it('uses the latest verification confidence as the final table column', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/supplier/overview')) return Promise.resolve(jsonResponse(overview));
      if (url.includes('/supplier/relations?')) return Promise.resolve(jsonResponse(relations));
      if (url.endsWith('/supplier/relations/relation-1/verifications')) {
        return Promise.resolve(jsonResponse(verifications));
      }
      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    }));

    render(<SupplierPage />);

    expect(await screen.findByLabelText('厂商选择')).toHaveValue('大疆');
    expect(screen.getByRole('columnheader', { name: '供应商' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '供应零件/模块' })).toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: /供应关系状态/ })).not.toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '供应关系来源' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /供应关系验证可信度/ })).toBeInTheDocument();
    expect(screen.getAllByText('明确')[0]).toHaveClass('verification-confidence-explicit');
    expect(screen.getByText('未验证')).toHaveClass('verification-confidence-unknown');

    fireEvent.click(screen.getByRole('button', { name: '展开欧菲光的验证记录' }));

    expect(await screen.findByText(/验证结论：确认/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '查看验证来源（1）' })).toHaveAttribute(
      'href',
      'https://example.test/verification',
    );
    expect(screen.getByRole('link', { name: 'https://example.test/primary-evidence' })).toHaveAttribute(
      'href',
      'https://example.test/primary-evidence',
    );
  });
});
