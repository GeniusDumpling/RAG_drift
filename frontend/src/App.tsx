import { useState } from 'react';

import { AiChatPage } from './pages/AiChatPage';
import { ContentDetailPage } from './pages/ContentDetailPage';
import { DashboardPage } from './pages/DashboardPage';
import { DatabasePage } from './pages/DatabasePage';
import { LiteratureResearchPanel } from './pages/LiteratureResearchPanel';
import { RunDetailPage } from './pages/RunDetailPage';
import { SearchPage } from './pages/SearchPage';
import { SourcesPage } from './pages/SourcesPage';

type View = 'Dashboard' | 'Sources' | 'Runs' | 'Search' | 'Answer' | 'Content' | 'Database' | 'Literature';

const NAV_ITEMS: View[] = ['Dashboard', 'Sources', 'Runs', 'Search', 'Answer', 'Content', 'Database', 'Literature'];

const NAV_LABELS: Record<View, string> = {
  Dashboard: '总览',
  Sources: '数据源',
  Runs: '运行记录',
  Search: '检索',
  Answer: 'AI 问答',
  Content: '内容详情',
  Database: '数据库',
  Literature: '文献研究',
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
            <strong>信息 RAG</strong>
            <p className="muted compact">信息检索增强问答系统</p>
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
        {activeView === 'Search' ? <SearchPage initialQuery={searchSeed} onOpenContent={openContent} mode="search" /> : null}
        {activeView === 'Answer' ? <AiChatPage initialQuery={searchSeed} onOpenContent={openContent} /> : null}
        {activeView === 'Content' ? <ContentDetailPage selectedContentId={selectedContentId} onOpenRun={openRun} /> : null}
        {activeView === 'Database' ? <DatabasePage /> : null}
        {activeView === 'Literature' ? <LiteratureResearchPanel /> : null}
      </main>
    </div>
  );
}

export default App;
