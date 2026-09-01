import {
  CheckCircleIcon,
  ClipboardDocumentListIcon,
  EyeIcon,
  MagnifyingGlassIcon,
  MegaphoneIcon,
  WrenchScrewdriverIcon,
} from '@heroicons/react/24/solid'
import type { BadgeVariant } from '@astryxdesign/core/Badge'

// Shared ticket-state vocabulary -- the Kanban board (TicketsGrid.tsx), the
// Tickets page's flat list/timeline, and TicketDetailDialog all key off the
// same six states from mcp_servers/shared/tickets.py's state machine, so
// their labels, icons, and colors live here once rather than drifting
// between views.

export const STATE_LABEL: Record<string, string> = {
  investigating: 'Investigating',
  planned: 'Planned',
  resolving: 'Resolving',
  in_review: 'In Review',
  completed: 'Completed',
  escalated: 'Escalated',
}

export const STATE_ICON: Record<string, typeof CheckCircleIcon> = {
  investigating: MagnifyingGlassIcon,
  planned: ClipboardDocumentListIcon,
  resolving: WrenchScrewdriverIcon,
  in_review: EyeIcon,
  completed: CheckCircleIcon,
  escalated: MegaphoneIcon,
}

// Colors are chosen to correlate with the *activity* that produces each
// state, not picked independently per state -- so the same color means the
// same thing everywhere in the app (Kanban border, timeline dot, plan/
// report event icon), not six unrelated hues that happen to be distinct.
//
// - investigating/planned unchanged: a submitted plan's own event icon is
//   already purple (StatusIcon.tsx's NAME_STYLE), which is where `planned`
//   already got its purple from -- no correlation bug there.
// - in_review used to be pink, uncorrelated with anything; a submitted
//   report's own event icon is blue (StatusIcon.tsx's NAME_STYLE) -- moved
//   in_review to match, since a report is what produces that state.
// - resolving and escalated each keep their own distinct color again --
//   astryx's palette has no literal "brown" token, so resolving uses
//   `orange` as the nearest available hue; escalated uses `yellow`.
export const STATE_COLOR: Record<string, { fg: string; bg: string }> = {
  investigating: { fg: 'var(--color-icon-cyan)', bg: 'var(--color-background-cyan)' },
  planned: { fg: 'var(--color-icon-purple)', bg: 'var(--color-background-purple)' },
  resolving: { fg: 'var(--color-icon-orange)', bg: 'var(--color-background-orange)' },
  in_review: { fg: 'var(--color-icon-blue)', bg: 'var(--color-background-blue)' },
  // Completed needs nothing further from anyone -- gray rather than a
  // "success" color, so a finished ticket doesn't compete visually with
  // ones that still need attention.
  completed: { fg: 'var(--color-icon-secondary)', bg: 'var(--color-background-muted)' },
  escalated: { fg: 'var(--color-icon-yellow)', bg: 'var(--color-background-yellow)' },
}

// Maps each ticket state to one of Badge's built-in tinted-background
// variants -- the same hues STATE_COLOR already picks, just named the way
// Badge's own variant prop expects them.
export const STATE_BADGE_VARIANT: Record<string, BadgeVariant> = {
  investigating: 'cyan',
  planned: 'purple',
  resolving: 'orange',
  in_review: 'blue',
  completed: 'neutral',
  escalated: 'yellow',
}
