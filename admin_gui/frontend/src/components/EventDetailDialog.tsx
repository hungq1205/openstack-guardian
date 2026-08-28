import { useEffect, useState, type ReactNode } from 'react'
import {
  CalendarIcon,
  ChatBubbleLeftRightIcon,
  CheckIcon,
  ClockIcon,
  TicketIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline'
import { Badge, type BadgeVariant } from '@astryxdesign/core/Badge'
import { Button } from '@astryxdesign/core/Button'
import { Dialog, DialogHeader } from '@astryxdesign/core/Dialog'
import { Layout, LayoutContent } from '@astryxdesign/core/Layout'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { TextArea } from '@astryxdesign/core/TextArea'
import { Heading, Text } from '@astryxdesign/core/Text'
import type { EventOut } from '../lib/api'
import { displayEventName } from '../lib/eventDisplay'
import { formatDuration, formatFullTime } from '../lib/time'
import { CodeInline, CodePanel } from './CodePanel'
import { FieldCards } from './FieldCards'

function MetaItem({ icon: Icon, children }: { icon: typeof ClockIcon; children: ReactNode }) {
  return (
    <HStack gap={1} align="center">
      <Icon style={{ width: 14, height: 14, color: 'var(--color-icon-secondary)' }} />
      <Text type="body" size="sm" color="secondary">
        {children}
      </Text>
    </HStack>
  )
}

const STATUS_BADGE_VARIANT: Record<string, BadgeVariant> = {
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
function statusLabel(status: string): string {
  return status === 'changes_requested' ? 'REJECTED' : status.toUpperCase()
}

// Only submit_investigation_plan's own handler reads a request-changes
// comment back out of its tool result and knows what to do with it (revise,
// resubmit) -- offering this action on any other gated tool would hand back
// a result shape the calling skill has no defined behavior for, so it's
// restricted to that one tool rather than every pending call.
const REVISABLE_TOOL_NAME = 'submit_investigation_plan'

interface FieldSpec {
  key: string
  label: string
}

// Field order matches the skill's own plan/report shape (see SKILL.md Step 5
// and Step 9) -- an admin reading this dialog should see the same structure
// the agent was asked to fill in, not an alphabetized JSON dump.
const PLAN_FIELDS: FieldSpec[] = [
  { key: 'evidence', label: 'Evidence' },
  { key: 'hypothesis', label: 'Hypothesis' },
  { key: 'reasoning', label: 'Reasoning' },
  { key: 'proposed_action', label: 'Proposed action' },
  { key: 'expected_result', label: 'Expected result' },
  { key: 'limitations', label: 'Limitations' },
]

const REPORT_FIELDS: FieldSpec[] = [
  { key: 'observed_failure', label: 'Observed failure' },
  { key: 'evidence', label: 'Evidence' },
  { key: 'root_cause_hypothesis', label: 'Root-cause hypothesis' },
  { key: 'confidence', label: 'Confidence' },
  { key: 'action_taken', label: 'Action taken' },
  { key: 'result', label: 'Result' },
  { key: 'remaining_uncertainty', label: 'Remaining uncertainty' },
]

const FRIENDLY_FIELDS_BY_TOOL: Record<string, FieldSpec[]> = {
  [REVISABLE_TOOL_NAME]: PLAN_FIELDS,
  submit_investigation_report: REPORT_FIELDS,
}

function parseArguments(argumentsJson: string): Record<string, unknown> {
  try {
    const value: unknown = JSON.parse(argumentsJson)
    if (value && typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>
  } catch {
    // not JSON, or not an object -- render nothing rather than guess
  }
  return {}
}

const META_FIELDS: FieldSpec[] = [
  { key: 'resource_id', label: 'Server' },
  { key: 'pattern_id', label: 'Pattern' },
]

function pickFields(parsed: Record<string, unknown>, specs: FieldSpec[]): { label: string; value: string }[] {
  return specs
    .map(({ key, label }) => ({ label, value: parsed[key] }))
    .filter((entry): entry is { label: string; value: string } => typeof entry.value === 'string' && !!entry.value)
}

// A plan or report read as a document (one card per field, a heading naming
// it, then its prose) rather than tool-call arguments (key: "value" lines
// meant for debugging a real API request) -- these two tools have no API
// request behind them at all, so a raw CodePanel dump would be actively
// misleading here, not just less readable.
function FriendlyFields({ argumentsJson, fields }: { argumentsJson: string; fields: FieldSpec[] }) {
  const parsed = parseArguments(argumentsJson)
  return <FieldCards meta={pickFields(parsed, META_FIELDS)} fields={pickFields(parsed, fields)} />
}

// The detail view for a logged tool/resource/prompt call: a status pill and
// icon+text meta row up top (time, duration), then either a labeled-fields
// document (submit_investigation_plan/submit_investigation_report) or the
// dark inspector panel every other tool's raw parameters/result get. When
// `onDecide` is provided and the call is still pending, Approve/Deny sit
// right under the status pill -- the same decision surface as the
// notification toast and the Logs row, just with full context in front of
// it. A pending plan additionally gets a standing comment box beneath its
// fields -- always visible, not behind a click, since asking the admin to
// find a toggle button first defeats the point of putting it in front of
// them at all.
export function EventDetailDialog({
  event,
  onClose,
  onDecide,
  onRequestChanges,
}: {
  event: EventOut | null
  onClose: () => void
  onDecide?: (approved: boolean) => void
  onRequestChanges?: (comment: string) => void
}) {
  const [comment, setComment] = useState('')

  // A fresh comment box per event, regardless of whether the dialog
  // actually unmounts between two different rows being opened in sequence.
  useEffect(() => {
    setComment('')
  }, [event?.id])

  function submitRequestChanges() {
    const trimmed = comment.trim()
    if (!trimmed || !onRequestChanges) return
    onRequestChanges(trimmed)
    setComment('')
  }

  // Approve/deny is a terminal decision -- once made, there's nothing left
  // in this dialog to act on, so close it the same way clicking the toast's
  // or the Logs row's own Approve/Deny already effectively does (they don't
  // have a dialog open to begin with).
  function decideAndClose(approved: boolean) {
    onDecide?.(approved)
    onClose()
  }

  return (
    <Dialog isOpen={event !== null} onOpenChange={(open) => !open && onClose()} width={680} purpose="info">
      {event && (
        <Layout
          header={
            <DialogHeader
              title={displayEventName(event.name)}
              subtitle={`${event.server} · ${event.kind}`}
              onOpenChange={onClose}
            />
          }
          content={
            <LayoutContent isScrollable>
              <VStack gap={4}>
                <HStack gap={4} align="center" wrap="wrap">
                  <Badge
                    variant={STATUS_BADGE_VARIANT[event.status] ?? 'success'}
                    label={statusLabel(event.status)}
                  />
                  <MetaItem icon={ClockIcon}>{formatDuration(event.duration_ms)}</MetaItem>
                  <MetaItem icon={CalendarIcon}>{formatFullTime(event.ts)}</MetaItem>
                  <MetaItem icon={TicketIcon}>
                    {event.ticket_id !== null ? `Ticket #${event.ticket_id}` : 'Unassigned'}
                  </MetaItem>
                </HStack>

                {event.status === 'pending' && onDecide && (
                  <HStack gap={2} wrap="wrap">
                    <Button
                      label="Approve"
                      variant="primary"
                      icon={<CheckIcon style={{ width: 16, height: 16 }} />}
                      onClick={() => decideAndClose(true)}
                    />
                    <Button
                      label="Deny"
                      variant="destructive"
                      icon={<XMarkIcon style={{ width: 16, height: 16 }} />}
                      onClick={() => decideAndClose(false)}
                    />
                  </HStack>
                )}

                {event.error_message && (
                  <VStack gap={1}>
                    {event.status === 'changes_requested' && (
                      <Text type="body" weight="bold" size="sm">
                        Admin comment
                      </Text>
                    )}
                    <Text
                      type="body"
                      style={{
                        color:
                          event.status === 'changes_requested'
                            ? 'var(--color-text-secondary)'
                            : 'var(--color-text-red)',
                      }}
                    >
                      {event.error_message}
                    </Text>
                  </VStack>
                )}

                {FRIENDLY_FIELDS_BY_TOOL[event.name] ? (
                  <FriendlyFields argumentsJson={event.arguments_json} fields={FRIENDLY_FIELDS_BY_TOOL[event.name]} />
                ) : (
                  <>
                    <HStack gap={2} align="center">
                      <Text type="body" weight="bold" size="sm">
                        Action
                      </Text>
                      {event.action ? (
                        <CodeInline>{event.action}</CodeInline>
                      ) : (
                        <Text type="body" color="secondary" size="sm">
                          local -- no external call
                        </Text>
                      )}
                    </HStack>
                    <VStack gap={2}>
                      <Heading level={4}>Parameters</Heading>
                      <CodePanel jsonText={event.arguments_json} emptyLabel="(none)" />
                    </VStack>
                    <VStack gap={2}>
                      <Heading level={4}>Result</Heading>
                      <CodePanel jsonText={event.result_summary} emptyLabel="(empty)" />
                    </VStack>
                  </>
                )}

                {event.status === 'pending' && event.name === REVISABLE_TOOL_NAME && onRequestChanges && (
                  <VStack gap={2}>
                    <hr style={{ width: '100%', border: 'none', borderTop: '1px solid var(--color-border)' }} />
                    <TextArea
                      label="Admin comment"
                      placeholder="What should change?"
                      value={comment}
                      onChange={setComment}
                      rows={3}
                    />
                    <Button
                      label="Send comment"
                      variant="secondary"
                      icon={<ChatBubbleLeftRightIcon style={{ width: 16, height: 16 }} />}
                      onClick={submitRequestChanges}
                      isDisabled={!comment.trim()}
                    />
                  </VStack>
                )}
              </VStack>
            </LayoutContent>
          }
        />
      )}
    </Dialog>
  )
}
