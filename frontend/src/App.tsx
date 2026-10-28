import { useState } from 'react';

import {
  BookOpen,
  Database,
  FileText,
  Factory,
  History,
  LayoutDashboard,
  MessageSquareText,
  Plane,
  Rss,
  Search,
  Settings,
  Waypoints,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { AiChatPage } from './pages/AiChatPage';
import { ContentDetailPage } from './pages/ContentDetailPage';
import { DashboardPage } from './pages/DashboardPage';
import { DatabasePage } from './pages/DatabasePage';
import { DronesPage } from './pages/DronesPage';
import { LiteratureResearchPanel } from './pages/LiteratureResearchPanel';
import { RunDetailPage } from './pages/RunDetailPage';
import { SearchPage } from './pages/SearchPage';
import { SourcesPage } from './pages/SourcesPage';
import { SupplierPage } from './pages/SupplierPage';
import { VideoGraphPage } from './pages/VideoGraphPage';

type View = 'Dashboard' | 'Sources' | 'Runs' | 'Search' | 'Answer' | 'Content' | 'Database' | 'Literature' | 'Supplier' | 'VideoGraph' | 'Drones';

const USER_NAV_ITEMS: View[] = ['Dashboard', 'Search', 'Answer', 'Drones', 'Supplier', 'Literature', 'VideoGraph'];
const ADMIN_NAV_ITEMS: View[] = ['Sources', 'Runs', 'Content', 'Database'];

const NAV_ICONS: Record<View, LucideIcon> = {
  Dashboard: LayoutDashboard,
  Search,
  Answer: MessageSquareText,
  Drones: Plane,
  Supplier: Factory,
  Literature: BookOpen,
  VideoGraph: Waypoints,
  Sources: Rss,
  Runs: History,
  Content: FileText,
  Database,
};

const NAV_LABELS: Record<View, string> = {
  Dashboard: '总览',
  Search: '检索',
  Answer: 'AI 问答',
  Drones: '无人机信息',
  Supplier: '供应链',
  Literature: '文献研究',
  VideoGraph: '视频知识图谱',
  Sources: '数据源',
  Runs: '运行记录',
  Content: '内容详情',
  Database: '数据库',
};

export function App() {
  const [activeView, setActiveView] = useState<View>(() => new URLSearchParams(window.location.search).get('view') === 'VideoGraph' ? 'VideoGraph' : 'Dashboard');
  const [selectedRunId, setSelectedRunId] = useState('');
  const [selectedContentId, setSelectedContentId] = useState('');
  const [adminExpanded, setAdminExpanded] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  const adminActive = ADMIN_NAV_ITEMS.includes(activeView);
  const adminVisible = adminExpanded || adminActive;

  function openRun(runId: string) {
    setSelectedRunId(runId);
    setActiveView('Runs');
  }

  function openContent(contentItemId: string) {
    setSelectedContentId(contentItemId);
    setActiveView('Content');
  }

  return (
    <div className={`app-shell${sidebarCollapsed ? ' sidebar-collapsed' : ''}${activeView === 'VideoGraph' ? ' app-shell--graph' : ''}`}>
      <aside className="nav" id="app-sidebar">
        <div className="sidebar-content" hidden={sidebarCollapsed}>
          <div className="brand-block">
            <span className="brand-dot" />
            <div>
              <strong>RAG系统控制台</strong>
            </div>
          </div>
          <nav aria-label="主导航 Primary">
            <div className="nav-user-section">
              {USER_NAV_ITEMS.map((item) => {
                const Icon = NAV_ICONS[item];
                return (
                  <button
                    aria-current={activeView === item ? 'page' : undefined}
                    className={activeView === item ? 'active' : ''}
                    key={item}
                    type="button"
                    onClick={() => setActiveView(item)}
                  >
                    <Icon aria-hidden="true" size={16} strokeWidth={1.8} />
                    <span>{NAV_LABELS[item] ?? item}</span>
                  </button>
                );
              })}
            </div>
            <div className="nav-admin-section">
              <button
                aria-controls="admin-navigation"
                aria-expanded={adminVisible}
                className="nav-section-toggle"
                type="button"
                onClick={() => setAdminExpanded((expanded) => !expanded)}
              >
                <span className="nav-section-label">
                  <Settings aria-hidden="true" size={14} strokeWidth={1.8} />
                  管理
                </span>
                <span aria-hidden="true">{adminVisible ? '−' : '+'}</span>
              </button>
              <div id="admin-navigation" hidden={!adminVisible}>
                {ADMIN_NAV_ITEMS.map((item) => {
                  const Icon = NAV_ICONS[item];
                  return (
                    <button
                      aria-current={activeView === item ? 'page' : undefined}
                      className={activeView === item ? 'active' : ''}
                      key={item}
                      type="button"
                      onClick={() => setActiveView(item)}
                    >
                      <Icon aria-hidden="true" size={16} strokeWidth={1.8} />
                      <span>{NAV_LABELS[item] ?? item}</span>
                    </button>
                  );
                })}
              </div>
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
      <div className={`workspace${activeView === 'VideoGraph' ? ' workspace--graph' : ''}`}>
        {activeView !== 'VideoGraph' ? <header className="app-topbar">
          <p>信息治理 / {NAV_LABELS[activeView]}</p>
          <div className="system-status"><span /> 系统在线</div>
        </header> : null}
        <main className={`main${activeView === 'VideoGraph' ? ' main--graph' : ''}`}>
          {activeView === 'Dashboard' ? <DashboardPage /> : null}
          {activeView === 'Sources' ? <SourcesPage /> : null}
          {activeView === 'Runs' ? <RunDetailPage selectedRunId={selectedRunId} /> : null}
          {activeView === 'Search' ? <SearchPage onOpenContent={openContent} mode="search" /> : null}
          {activeView === 'Answer' ? <AiChatPage onOpenContent={openContent} /> : null}
          {activeView === 'Content' ? <ContentDetailPage selectedContentId={selectedContentId} onOpenRun={openRun} /> : null}
          {activeView === 'Database' ? <DatabasePage /> : null}
          {activeView === 'Drones' ? <DronesPage /> : null}
          {activeView === 'Literature' ? <LiteratureResearchPanel /> : null}
          {activeView === 'Supplier' ? <SupplierPage /> : null}
          {activeView === 'VideoGraph' ? <VideoGraphPage onOpenContent={openContent} /> : null}
        </main>
      </div>
    </div>
  );
}

export default App;
