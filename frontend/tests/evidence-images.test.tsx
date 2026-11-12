import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EvidenceImages } from '../src/components/EvidenceImages';
import type { EvidenceObject } from '../src/api/types';

describe('source bound repair images', () => {
  beforeEach(() => vi.stubEnv('VITE_API_BASE_URL', ''));
  afterEach(() => vi.unstubAllEnvs());

  it('loads assets from the API origin when frontend and API use different ports', () => {
    vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:8000/');
    const sha = 'c'.repeat(64);
    const evidence = [{ images: [{ asset_id: sha, url: `/ifixit-media/assets/${sha}`,
      caption: 'DJI Mavic 3 Pro', guide_url: 'https://www.ifixit.com/Guide/161708',
      width: 800, height: 600 }] }] as EvidenceObject[];
    render(<EvidenceImages evidence={evidence} />);
    expect(screen.getByRole('img').getAttribute('src')).toBe(`http://localhost:8000/ifixit-media/assets/${sha}`);
    expect(screen.getByRole('link', { name: '查看原图：DJI Mavic 3 Pro' }).getAttribute('href'))
      .toBe(`http://localhost:8000/ifixit-media/assets/${sha}`);
  });

  it('only displays cited evidence while preserving original citation numbers', () => {
    const make = (char: string, caption: string) => ({ asset_id: char.repeat(64),
      url: `/ifixit-media/assets/${char.repeat(64)}`, caption, step_order: 1,
      guide_url: 'https://www.ifixit.com/Guide/161708', width: 100, height: 100 });
    const evidence = [{ images: [make('a', 'DJI Mini 3')] },
      { images: [make('b', 'DJI Mavic 3 Pro')] }] as EvidenceObject[];
    render(<EvidenceImages evidence={evidence} citedIndices={[2]} />);
    expect(screen.getAllByRole('img')).toHaveLength(1);
    expect(screen.getByRole('img').getAttribute('alt')).toBe('DJI Mavic 3 Pro');
    expect(screen.getByText(/\[2\]/)).toBeTruthy();
  });
  it('shows exact local assets, source and step; rejects untrusted URLs and duplicates', () => {
    const sha = 'a'.repeat(64);
    const image = { asset_id: sha, source_guid: 'HB5apup35uvAbdWi',
      url: `/ifixit-media/assets/${sha}`, source_url: 'https://guide-images.cdn.ifixit.com/igi/x.full',
      guide_url: 'https://www.ifixit.com/Guide/161708', step_order: 1,
      step_title: 'Drone Main Board Side A', caption: 'Drone Main Board Side A',
      width: 2164, height: 1623 };
    const evidence = [{ images: [image, image, { ...image, asset_id: 'b'.repeat(64), url: 'https://bad.test/x.jpg' }] }] as EvidenceObject[];
    render(<EvidenceImages evidence={evidence} />);
    expect(screen.getAllByRole('img')).toHaveLength(1);
    expect(screen.getByRole('img').getAttribute('src')).toBe(image.url);
    expect(screen.getByText(/步骤 1/)).toBeTruthy();
    expect(screen.getByRole('link', { name: '原始指南' }).getAttribute('href')).toBe(image.guide_url);
  });
});
