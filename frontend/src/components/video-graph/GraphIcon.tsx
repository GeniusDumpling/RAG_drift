import type { CSSProperties } from 'react';

const paths: Record<string, string[]> = {
  graph: ['M8 6l8 3M7 8l3 9m6-6l-4 6', 'M7 5a2 2 0 1 0 0 .1M18 9a2 2 0 1 0 0 .1M12 19a2 2 0 1 0 0 .1'],
  search: ['M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13Zm5-1 5 5'],
  grid: ['M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z'],
  panel: ['M4 4h16v16H4zM14 4v16M17 8h1m-1 4h1m-1 4h1'],
  refresh: ['M20 8a8 8 0 0 0-14-3L3 8m0-5v5h5M4 16a8 8 0 0 0 14 3l3-3m0 5v-5h-5'],
  close: ['m6 6 12 12M6 18 18 6'],
  arrow: ['M5 12h14m-5-5 5 5-5 5'],
  expand: ['M8 4H4v4m12-4h4v4M4 16v4h4m12-4v4h-4'],
  chevron: ['m9 5 7 7-7 7'],
  link: ['m10 13 4-4m-6 6-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m4-2 2-2a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0'],
  layers: ['m12 3 10 5-10 5L2 8l10-5Zm-10 9 10 5 10-5M2 16l10 5 10-5'],
  settings: ['M4 7h16M4 17h16M9 4v6m6 4v6'],
  info: ['M12 17v-5m0-5h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0'],
};
export function GraphIcon({ name, size = 18, style }: { name: string; size?: number; style?: CSSProperties }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={style}>
    {(paths[name] ?? paths.graph).map((d, index) => <path d={d} key={index} />)}
  </svg>;
}
