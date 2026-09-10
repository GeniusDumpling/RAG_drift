import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

const styles = readFileSync('src/styles.css', 'utf8');

describe('supplier verification evidence styles', () => {
  it('keeps expanded evidence text visible on its light surface', () => {
    expect(styles).toMatch(/\.evidence-pre\s*\{[^}]*color:\s*#161616/);
  });
});
