import { useState } from 'react';

import { ContentDetailPage } from './pages/ContentDetailPage';
import { DashboardPage } from './pages/DashboardPage';
import { RunDetailPage } from './pages/RunDetailPage';
import { SearchPage } from './pages/SearchPage';
import { SourcesPage } from './pages/SourcesPage';

type View = 'Dashboard' | 'Sources' | 'Runs' | 'Search' | 'Content';

const NAV_ITEMS: View[] = ['Dashboard', 'Sources', 'Runs', 'Search', 'Content'];

export function App() {
  const [activeView, setActiveView] = useState<View>('Dashboard');
  const [searchSeed, setSearchSeed] = useState('');

  function openSearch(query: string) {
    setSearchSeed(query);
    setActiveView('Search');
  }

  return (
    <div className="app-shell">
      <aside className="nav">
        <div className="brand-block">
          <span className="brand-dot" />
          <div>
            <strong>Intel RAG</strong>
            <p className="muted compact">observable OSINT prototype</p>
          </div>
        </div>
        {NAV_ITEMS.map((item) => (
          <button
            className={activeView === item ? 'active' : ''}
            key={item}
            type="button"
            onClick={() => setActiveView(item)}
          >
            {item}
          </button>
        ))}
      </aside>
      <main className="main">
        {activeView === 'Dashboard' ? <DashboardPage onSearch={openSearch} /> : null}
        {activeView === 'Sources' ? <SourcesPage /> : null}
        {activeView === 'Runs' ? <RunDetailPage /> : null}
        {activeView === 'Search' ? <SearchPage initialQuery={searchSeed} /> : null}
        {activeView === 'Content' ? <ContentDetailPage /> : null}
      </main>
    </div>
  );
}

export default App;
