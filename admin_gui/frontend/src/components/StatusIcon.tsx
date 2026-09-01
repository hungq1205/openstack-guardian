import {
  ArrowPathIcon,
  CheckIcon,
  ClipboardDocumentListIcon,
  ClockIcon,
  DocumentTextIcon,
  NoSymbolIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline'

const STATUS_STYLE: Record<string, { bg: string; fg: string; Icon: typeof CheckIcon }> = {
  success: { bg: 'var(--color-background-green)', fg: 'var(--color-icon-green)', Icon: CheckIcon },
  error: { bg: 'var(--color-background-red)', fg: 'var(--color-icon-red)', Icon: XMarkIcon },
  pending: { bg: 'var(--color-background-orange)', fg: 'var(--color-icon-orange)', Icon: ClockIcon },
  approved: { bg: 'var(--color-background-orange)', fg: 'var(--color-icon-orange)', Icon: ArrowPathIcon },
  denied: { bg: 'var(--color-background-gray)', fg: 'var(--color-icon-gray)', Icon: NoSymbolIcon },
  // A rejected plan (see request-changes on submit_investigation_plan) reads
  // the same as a denial here -- the distinction that matters (a comment is
  // attached, inviting a resubmit) lives in the label text and the detail
  // dialog's body, not in its own icon treatment.
  changes_requested: { bg: 'var(--color-background-gray)', fg: 'var(--color-icon-gray)', Icon: NoSymbolIcon },
}

// A plan or report isn't a pass/fail action -- it has no real "succeeded" or
// "failed" outcome of its own, just a record. Once status is past the point
// where an operator's decision could still change anything (still pending,
// denied, or rejected-with-comment -- all handled by STATUS_STYLE above),
// these two get their own fixed icon instead of the green success checkmark
// every other tool call earns by actually completing without error.
//
// Exported so TicketDetailDialog's plan/report list uses this exact same
// color/glyph per tool, rather than a second color choice that could drift.
export const NAME_STYLE: Record<string, { bg: string; fg: string; Icon: typeof CheckIcon }> = {
  submit_investigation_plan: {
    bg: 'var(--color-background-purple)',
    fg: 'var(--color-icon-purple)',
    Icon: ClipboardDocumentListIcon,
  },
  submit_investigation_report: {
    bg: 'var(--color-background-blue)',
    fg: 'var(--color-icon-blue)',
    Icon: DocumentTextIcon,
  },
}

const NAME_OVERRIDABLE_STATUSES = new Set(['success', 'approved'])
const REJECTED_STATUSES = new Set(['denied', 'changes_requested'])

// The timeline's per-entry status mark -- green check (succeeded), red X
// (failed), orange clock (awaiting an operator's approve/deny decision),
// orange refresh (approved, now actually running). Unlike the dashboard's
// activity table (which never signals status via color, only the trailing
// red bar), this page's reference explicitly uses a colored icon per row
// for what happened, so status lives here instead.
//
// A plan or report keeps its own fixed color and glyph (NAME_STYLE) no
// matter the status -- including once denied/rejected, where a small red
// cross badges onto the corner instead of swapping the glyph away to a
// generic gray slash. A rejected report still reads as "a report" (blue
// document), with the rejection layered on top rather than erasing what it
// was. Any other tool call that gets denied (no NAME_STYLE entry) keeps the
// plain gray slash, unbadged.
//
// Deliberately NOT colored by the event's ticket state: this feed is a
// chronological, multi-ticket list (Recent activity, Logs), and the same
// resource/action label recurs across many different tickets that are each
// sitting in a different state today -- coloring by ticket state there
// just produces an arbitrary-looking scatter of colors with no row-level
// explanation of which ticket owns which color. Ticket-state color stays
// where it's actually legible: one ticket at a time (its Kanban card, its
// own detail dialog, its own timeline).
export function StatusIcon({ status, name, size = 32 }: { status: string; name?: string; size?: number }) {
  const nameStyle = name ? NAME_STYLE[name] : undefined
  const isRejected = REJECTED_STATUSES.has(status)
  const glyphSource =
    nameStyle && (NAME_OVERRIDABLE_STATUSES.has(status) || isRejected)
      ? nameStyle
      : (STATUS_STYLE[status] ?? STATUS_STYLE.success)
  const Icon = glyphSource.Icon
  const bg = nameStyle?.bg ?? glyphSource.bg
  const fg = nameStyle?.fg ?? glyphSource.fg
  const showRejectedBadge = isRejected && nameStyle
  const badgeSize = Math.round(size * 0.5)
  return (
    <div style={{ position: 'relative', width: size, height: size, flexShrink: 0 }}>
      <div
        style={{
          width: size,
          height: size,
          borderRadius: '50%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: bg,
        }}
      >
        <Icon style={{ width: size * 0.5, height: size * 0.5, color: fg }} />
      </div>
      {showRejectedBadge && (
        <div
          style={{
            position: 'absolute',
            bottom: -2,
            right: -2,
            width: badgeSize,
            height: badgeSize,
            borderRadius: '50%',
            border: '2px solid var(--color-background-card)',
            background: 'var(--color-background-red)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <XMarkIcon style={{ width: badgeSize * 0.6, height: badgeSize * 0.6, color: 'var(--color-icon-red)' }} />
        </div>
      )}
    </div>
  )
}
