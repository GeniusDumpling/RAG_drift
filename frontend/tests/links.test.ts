import { describe, expect, it } from 'vitest';

import { safeExternalHref } from '../src/utils/links';

describe('safeExternalHref', () => {
  it('allows http and https URLs', () => {
    expect(safeExternalHref('https://example.test/docs/page')).toBe('https://example.test/docs/page');
    expect(safeExternalHref('http://example.test/docs/page')).toBe('http://example.test/docs/page');
  });

  it('rejects invalid, relative, and non-http URLs', () => {
    expect(safeExternalHref('javascript:alert(1)')).toBeNull();
    expect(safeExternalHref('data:text/html,<script>alert(1)</script>')).toBeNull();
    expect(safeExternalHref('mailto:ops@example.test')).toBeNull();
    expect(safeExternalHref('/relative/path')).toBeNull();
    expect(safeExternalHref('not a url')).toBeNull();
    expect(safeExternalHref('')).toBeNull();
    expect(safeExternalHref(null)).toBeNull();
    expect(safeExternalHref(undefined)).toBeNull();
  });
});
