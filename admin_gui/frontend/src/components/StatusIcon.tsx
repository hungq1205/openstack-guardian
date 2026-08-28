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
const NAME_STYLE: Record<string, { bg: string; fg: string; Icon: typeof CheckIcon }> = {
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

// The timeline's per-entry status mark -- green check (succeeded), red X
// (failed), orange clock (awaiting an operator's approve/deny decision),
// orange refresh (approved, now actually running), gray slash (denied by
// the operator). Unlike the dashboard's activity table (which never signals
// status via color, only the trailing red bar), this page's reference
// explicitly uses a colored icon per row for what happened, so status lives
// here instead. `name` lets a plan/report override that once it's past the
// decision point -- see NAME_STYLE.
export function StatusIcon({ status, name, size = 32 }: { status: string; name?: string; size?: number }) {
  const nameStyle = name ? NAME_STYLE[name] : undefined
  const { bg, fg, Icon } =
    nameStyle && NAME_OVERRIDABLE_STATUSES.has(status) ? nameStyle : (STATUS_STYLE[status] ?? STATUS_STYLE.success)
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: bg,
      }}
    >
      <Icon style={{ width: size * 0.5, height: size * 0.5, color: fg }} />
    </div>
  )
}
