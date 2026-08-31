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

const NAV_ITEMS: View[] = ['Dashboard', 'Sources', 'Runs', 'Search', 'Answer', 'Literature', 'Content', 'Database'];

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
  const [workspaceExpanded, setWorkspaceExpanded] = useState(true);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

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
    <div className={`app-shell${sidebarCollapsed ? ' sidebar-collapsed' : ''}`}>
      <aside className="nav" id="app-sidebar">
        <div className="sidebar-content" hidden={sidebarCollapsed}>
          <div className="brand-block">
            <span className="brand-dot" />
            <div>
              <strong>RAG系统控制台</strong>
            </div>
          </div>
          <nav aria-label="主导航 Primary">
            <button
              aria-controls="workspace-navigation"
              aria-expanded={workspaceExpanded}
              className="nav-section-toggle"
              type="button"
              onClick={() => setWorkspaceExpanded((expanded) => !expanded)}
            >
              <span>工作空间</span>
              <span aria-hidden="true">{workspaceExpanded ? '−' : '+'}</span>
            </button>
            <div id="workspace-navigation" hidden={!workspaceExpanded}>
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
            </div>
          </nav>
        </div>
        <button
          aria-controls="app-sidebar"
          aria-expanded={!sidebarCollapsed}
          className="sidebar-toggle"
          type="button"
          onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}
        >
          <span className="sr-only">{sidebarCollapsed ? '展开左侧导航' : '收起左侧导航'}</span>
          <span aria-hidden="true">{sidebarCollapsed ? '›' : '‹'}</span>
        </button>
      </aside>
      <div className="workspace">
        <header className="app-topbar">
          <p>信息治理 / {NAV_LABELS[activeView]}</p>
          <div className="system-status"><span /> 系统在线</div>
        </header>
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
    </div>
  );
}

export default App;
