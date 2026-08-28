import { Outlet, useLocation } from 'react-router-dom'
import { AppShell } from '@astryxdesign/core/AppShell'
import { NavIcon } from '@astryxdesign/core/NavIcon'
import { SideNav, SideNavItem, SideNavSection } from '@astryxdesign/core/SideNav'
import { TopNav, TopNavHeading } from '@astryxdesign/core/TopNav'
import { MediaTheme } from '@astryxdesign/core/theme'
import {
  ChartBarIcon,
  ClipboardDocumentListIcon,
  Cog6ToothIcon,
  DocumentTextIcon,
  ShieldCheckIcon,
} from '@heroicons/react/24/outline'
import { HomeIcon, ShieldCheckIcon as ShieldCheckIconSolid } from '@heroicons/react/24/solid'
import { NotificationCenter } from '../components/NotificationCenter'
import { PendingApprovalsProvider } from '../lib/pendingApprovals'

const NAV_ITEMS = [
  { href: '/', label: 'Dashboard', icon: HomeIcon },
  { href: '/logs', label: 'Logs', icon: DocumentTextIcon },
] as const

const CONFIG_NAV_ITEMS = [
  { href: '/catalog', label: 'Catalog', icon: ClipboardDocumentListIcon },
  { href: '/masking', label: 'Masking', icon: ShieldCheckIcon },
  { href: '/connections', label: 'Connections', icon: ChartBarIcon },
  { href: '/spec-sources', label: 'Spec Sources', icon: Cog6ToothIcon },
  { href: '/failure-patterns', label: 'Failure Patterns', icon: ClipboardDocumentListIcon },
] as const

// The top bar is a fixed dark strip regardless of light/dark theme mode --
// the one piece of chrome that never changes -- so its content is wrapped
// in a dark MediaTheme context to keep text/icons legible on it either way.
//
// PendingApprovalsProvider wraps the whole shell (not just NotificationCenter)
// so every page -- Logs, Dashboard -- can pull the same live pending list and
// `decide()` function via usePendingApprovals() rather than each opening its
// own SSE connection.
export function Shell() {
  const location = useLocation()

  return (
    <PendingApprovalsProvider>
      <AppShell
        height="fill"
        variant="wash"
        contentPadding={6}
        mobileNav={false}
        topNav={
          <div style={{ backgroundColor: '#0B0C10' }}>
            <MediaTheme mode="dark">
              <TopNav
                label="Main navigation"
                heading={
                  <TopNavHeading
                    heading="MCP Console"
                    logo={
                      <NavIcon
                        icon={<ShieldCheckIconSolid style={{ width: 16, height: 16, color: '#A5B4FC' }} />}
                      />
                    }
                  />
                }
              />
            </MediaTheme>
          </div>
        }
        sideNav={
          <div style={{ height: '100%', borderInlineEnd: '1px solid var(--color-border)' }}>
            <SideNav>
              <SideNavSection title="Monitor">
                {NAV_ITEMS.map((item) => (
                  <SideNavItem
                    key={item.href}
                    label={item.label}
                    icon={item.icon}
                    href={item.href}
                    isSelected={location.pathname === item.href}
                  />
                ))}
              </SideNavSection>
              <SideNavSection title="Configure">
                {CONFIG_NAV_ITEMS.map((item) => (
                  <SideNavItem
                    key={item.href}
                    label={item.label}
                    icon={item.icon}
                    href={item.href}
                    isSelected={location.pathname === item.href}
                  />
                ))}
              </SideNavSection>
            </SideNav>
          </div>
        }
      >
        <Outlet />
      </AppShell>
      <NotificationCenter />
    </PendingApprovalsProvider>
  )
}
