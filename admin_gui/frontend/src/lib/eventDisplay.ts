import type { BadgeVariant } from '@astryxdesign/core/Badge'

// Friendly titles for the two investigation-reporting tools -- an admin
// reading "submit_investigation_plan" everywhere it appears (dialog title,
// timeline row, toast, pending panel) gets no benefit from the literal tool
// name; "Plan"/"Report" says the same thing in the vocabulary they actually
// think in. Every other tool keeps its real name, since that name is what
// an admin would search the catalog for.
const FRIENDLY_NAMES: Record<string, string> = {
  submit_investigation_plan: 'Plan',
  submit_investigation_report: 'Report',
}

export function displayEventName(name: string): string {
  return FRIENDLY_NAMES[name] ?? name
}

// The tool catalog's own description (written for an agent deciding whether
// to call the tool, e.g. "Submit the pre-approval investigation plan
// (evidence, hypothesis, reasoning, proposed action, expected result,
// limitations) for operator review...") is dead weight once the UI already
// renders the friendly title and the full field-by-field body underneath --
// only look it up for tools that keep their own real name.
export function catalogDescriptionFor(name: string, descriptions: Record<string, string>): string | undefined {
  if (name in FRIENDLY_NAMES) return undefined
  return descriptions[name]
}

// Plan/Report calls are the ones an admin is meant to act on (or at least
// read) the moment they land -- worth popping the detail dialog open
// automatically when one arrives while the admin is already on the page,
// rather than making them notice and click the row themselves.
export function isAutoOpenEvent(name: string): boolean {
  return name in FRIENDLY_NAMES
}

// A ticket card's "what just happened" caption -- e.g. "Plan approved",
// "rebuild_server succeeded" -- read off the ticket's most recent event's
// raw `status` column. For Plan/Report specifically, a terminal `success`
// status *is* "approved": once an operator approves a gated call,
// `instrument_dispatch` overwrites the row's status with the real dispatch
// outcome (see `telemetry.resolve_event`), so "approved" never survives as a
// persisted value on its own -- "success" is the correct signal to read it
// back as approval for these two tools specifically.
const _STATUS_VERB: Record<string, string> = {
  pending: 'awaiting approval',
  denied: 'denied',
  changes_requested: 'changes requested',
  error: 'failed',
}

export function describeLatestAction(name: string, status: string): string {
  const isLifecycleTool = name in FRIENDLY_NAMES
  const verb = _STATUS_VERB[status] ?? (isLifecycleTool ? 'approved' : 'succeeded')
  return `${displayEventName(name)} ${verb}`
}

// Shared between EventDetailDialog and TicketDetailDialog's plan/report
// list -- one event-status vocabulary, not two that could drift apart.
export const STATUS_BADGE_VARIANT: Record<string, BadgeVariant> = {
  success: 'success',
  error: 'error',
  pending: 'warning',
  approved: 'warning',
  denied: 'neutral',
  changes_requested: 'neutral',
}

// A rejected plan (request-changes on submit_investigation_plan) carries the
// same badge severity as a flat denial, but its own label -- REJECTED --
// rather than the raw "CHANGES_REQUESTED" status string, so it reads as
// "not approved as-is" at a glance without being confused with a plain no.
export function statusLabel(status: string): string {
  return status === 'changes_requested' ? 'REJECTED' : status.toUpperCase()
}
