import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { EvidenceCard } from '../src/components/EvidenceCard';
import type { EvidenceObject } from '../src/api/types';

function evidence(): EvidenceObject {
  return {
    chunk_id: 'chunk', content_item_id: 'topic', raw_page_id: 'raw', source_site_id: 'px4',
    title: 'GPS drift', snippet: 'I tried the configuration and it worked.',
    canonical_url: 'https://discuss.px4.io/t/7/3', source_site_name: 'PX4',
    author_name: 'reporter', published_at: null, item_type: 'thread', score: 1,
    vector_score: null, keyword_score: null, matched_by: 'graph',
    thread_summary: null, video_url: null, description_text: null,
    forum_graph_evidence: [{
      id: 'proof', fact_id: 'fact', source_id: 'forum:px4:7', content_item_id: 'topic',
      post_id: 103, post_number: 3, author: 'reporter',
      post_url: 'https://discuss.px4.io/t/7/3', quote: 'I tried the configuration and it worked.',
      case_id: 'case', claim_id: 'claim', role: 'feedback', act: 'test_report',
      assertion: 'asserted', subject: 'PX4', predicate: 'instruction', object: null,
      value: 'Try the configuration', qualifiers: { firmware: '1.14' },
      outcome: 'reported_success', combined_operations: true, accepted_answer: true,
    }],
  };
}

afterEach(cleanup);

describe('forum graph evidence', () => {
  it('shows exact floor, conditions, bounded feedback and combined-operation caveat', () => {
    render(<EvidenceCard evidence={evidence()} />);
    fireEvent.click(screen.getByText('查看论坛案例与验证反馈'));
    expect(screen.getByText('图谱关联')).toBeTruthy();
    expect(screen.getByText(/reporter · 第 3 楼/)).toBeTruthy();
    expect(screen.getByText(/作者报告成功（仅该案例）/)).toBeTruthy();
    expect(screen.getByText(/多项操作的联合结果/)).toBeTruthy();
    expect(screen.getByText(/固件：1.14/)).toBeTruthy();
    expect(screen.getByText(/社区已采纳；有效范围仍以具体条件和反馈为准/)).toBeTruthy();
    expect(screen.getByRole('link', { name: '查看该楼层原文' }).getAttribute('href'))
      .toBe('https://discuss.px4.io/t/7/3');
  });

  it('does not label an untested recommendation as successful', () => {
    const item = evidence();
    item.forum_graph_evidence![0] = { ...item.forum_graph_evidence![0], role: 'claim',
      act: 'recommendation', outcome: null, combined_operations: false, accepted_answer: false };
    render(<EvidenceCard evidence={item} />);
    fireEvent.click(screen.getByText('查看论坛案例与验证反馈'));
    expect(screen.getByText(/尚无明确验证反馈/)).toBeTruthy();
    expect(screen.queryByText(/作者报告成功/)).toBeNull();
  });

  it('does not attach an action claim to a context floor or render executable links', () => {
    const item = evidence();
    item.forum_graph_evidence![0] = { ...item.forum_graph_evidence![0], role: 'context',
      post_url: 'javascript:alert(1)' };
    render(<EvidenceCard evidence={item} />);
    fireEvent.click(screen.getByText('查看论坛案例与验证反馈'));
    expect(screen.getByText('案例背景（操作建议另见对应楼层）')).toBeTruthy();
    expect(screen.queryByRole('link', { name: '查看该楼层原文' })).toBeNull();
  });

  it('shows readable conditions once and omits internal case identifiers', () => {
    const item = evidence();
    item.forum_graph_evidence![0].qualifiers = {
      firmware_version: '1.14', 'context:firmware': '1.14', case_id: 'internal-case-id',
    };
    render(<EvidenceCard evidence={item} />);
    fireEvent.click(screen.getByText('查看论坛案例与验证反馈'));
    expect(screen.getAllByText('固件：1.14')).toHaveLength(1);
    expect(screen.queryByText(/internal-case-id/)).toBeNull();
  });
});
