import { useState } from 'react';

import { ContentDetailPage } from './pages/ContentDetailPage';
import { DashboardPage } from './pages/DashboardPage';
import { DatabasePage } from './pages/DatabasePage';
import { RunDetailPage } from './pages/RunDetailPage';
import { SearchPage } from './pages/SearchPage';
import { SourcesPage } from './pages/SourcesPage';

type View = 'Dashboard' | 'Sources' | 'Runs' | 'Search' | 'Content' | 'Database';

const NAV_ITEMS: View[] = ['Dashboard', 'Sources', 'Runs', 'Search', 'Content', 'Database'];

const NAV_LABELS: Record<View, string> = {
  Dashboard: '总览 Dashboard',
  Sources: '数据源 Sources',
  Runs: '运行记录 Runs',
  Search: '检索问答 Search',
  Content: '内容详情 Content',
  Database: '数据库 Database',
};

export function App() {
  const [activeView, setActiveView] = useState<View>('Dashboard');
  const [searchSeed, setSearchSeed] = useState('');
  const [selectedRunId, setSelectedRunId] = useState('');
  const [selectedContentId, setSelectedContentId] = useState('');

  function openSearch(query: string) {
    setSearchSeed(query);
    setActiveView('Search');
  }

  function openRun(runId: string) {
    setSelectedRunId(runId);
    setActiveView('Runs');
  }

  function openContent(contentItemId: string) {
    setSelectedContentId(contentItemId);
    setActiveView('Content');
  }

  return (
    <div className="app-shell">
      <aside className="nav">
        <div className="brand-block">
          <span className="brand-dot" />
          <div>
            <strong>情报 RAG</strong>
            <p className="muted compact">可观测 OSINT 原型</p>
          </div>
        </div>
        <nav aria-label="主导航 Primary">
          {NAV_ITEMS.map((item) => (
            <button
              aria-current={activeView === item ? 'page' : undefined}
              className={activeView === item ? 'active' : ''}
              key={item}
              type="button"
              onClick={() => setActiveView(item)}
            >
              {NAV_LABELS[item] ?? item}
            </button>
          ))}
        </nav>
      </aside>
      <main className="main">
        {activeView === 'Dashboard' ? <DashboardPage onSearch={openSearch} /> : null}
        {activeView === 'Sources' ? <SourcesPage /> : null}
        {activeView === 'Runs' ? <RunDetailPage selectedRunId={selectedRunId} /> : null}
        {activeView === 'Search' ? <SearchPage initialQuery={searchSeed} onOpenContent={openContent} /> : null}
        {activeView === 'Content' ? <ContentDetailPage selectedContentId={selectedContentId} onOpenRun={openRun} /> : null}
        {activeView === 'Database' ? <DatabasePage /> : null}
      </main>
    </div>
  );
}

export default App;
