import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import '@astryxdesign/core/reset.css'
import '@astryxdesign/core/astryx.css'
import './index.css'
import { Theme } from '@astryxdesign/core/theme'
import { LinkProvider } from '@astryxdesign/core/Link'
import { cmpConsoleTheme } from './theme/cmpConsoleTheme'
import { RouterLink } from './router/RouterLink'
import { Shell } from './shell/Shell'
import { DashboardPage } from './pages/DashboardPage'
import { CatalogPage } from './pages/CatalogPage'
import { LogsPage } from './pages/LogsPage'
import { TicketsPage } from './pages/TicketsPage'
import { MaskingPage } from './pages/MaskingPage'
import { ConnectionsPage } from './pages/ConnectionsPage'
import { SpecSourcesPage } from './pages/SpecSourcesPage'
import { FailurePatternsPage } from './pages/FailurePatternsPage'
import { SettingsPage } from './pages/SettingsPage'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Theme theme={cmpConsoleTheme} mode="light">
      <LinkProvider component={RouterLink}>
        <BrowserRouter>
          <Routes>
            <Route element={<Shell />}>
              <Route index element={<DashboardPage />} />
              <Route path="tickets" element={<TicketsPage />} />
              <Route path="catalog" element={<CatalogPage />} />
              <Route path="logs" element={<LogsPage />} />
              <Route path="masking" element={<MaskingPage />} />
              <Route path="connections" element={<ConnectionsPage />} />
              <Route path="spec-sources" element={<SpecSourcesPage />} />
              <Route path="failure-patterns" element={<FailurePatternsPage />} />
              <Route path="settings" element={<SettingsPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </LinkProvider>
    </Theme>
  </StrictMode>,
)
