import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowTopRightOnSquareIcon,
  CalendarIcon,
  ChatBubbleLeftIcon,
  CheckIcon,
  MapPinIcon,
  PencilIcon,
  TrashIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline'
import { Badge } from '@astryxdesign/core/Badge'
import { Button } from '@astryxdesign/core/Button'
import { Dialog, DialogHeader } from '@astryxdesign/core/Dialog'
import { Layout, LayoutContent } from '@astryxdesign/core/Layout'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Heading, Text } from '@astryxdesign/core/Text'
import { TextInput } from '@astryxdesign/core/TextInput'
import { api, type EventOut, type TicketOut } from '../lib/api'
import { CodeInline } from './CodePanel'
import { EventDetailDialog } from './EventDetailDialog'
import { displayEventName, describeLatestAction } from '../lib/eventDisplay'
import { usePendingApprovals } from '../lib/pendingApprovals'
import { NAME_STYLE, StatusIcon } from './StatusIcon'
import { formatFullTime, formatOverviewTime } from '../lib/time'
import { STATE_BADGE_VARIANT, STATE_ICON, STATE_LABEL } from '../lib/ticketState'

const PLAN_REPORT_NAMES = new Set(['submit_investigation_plan', 'submit_investigation_report'])

// Icon + label meta row, same shape as EventDetailDialog's own MetaItem.
function MetaItem({ icon: Icon, children }: { icon: typeof CalendarIcon; children: ReactNode }) {
  return (
    <HStack gap={1} align="center">
      <Icon style={{ width: 14, height: 14, color: 'var(--color-icon-secondary)' }} />
      <Text type="body" size="sm" color="secondary">
        {children}
      </Text>
    </HStack>
  )
}

// A ticket's plan/report submissions, most-recent-first -- there can be more
// than one of each (a rejected plan gets revised and resubmitted, a report
// can prompt the investigation to be redone from a new plan), so this is a
// history, not a single current snapshot. Each row opens the same
// EventDetailDialog used everywhere else in the app for the full
// field-by-field view -- including its own Approve/Deny/Request-changes for
// a still-pending submission -- rather than duplicating that here.
//
// Colored by what each row *is* (NAME_STYLE's fixed plan/report color), not
// by the ticket's current state -- a plan submitted while investigating
// doesn't retroactively change color once the ticket moves on to resolving,
// and the same fixed color/rejected-badge is what Recent activity's own
// StatusIcon already uses for these two tools everywhere else in the app.
function PlanReportHistory({ ticketId, onChanged }: { ticketId: number; onChanged: () => void }) {
  const [events, setEvents] = useState<EventOut[]>([])
  const [selected, setSelected] = useState<EventOut | null>(null)
  const { decide, requestChanges } = usePendingApprovals()

  function reload() {
    api
      .listEvents({ ticket_id: String(ticketId), limit: 200 })
      .then((data) =>
        setEvents(data.filter((event) => PLAN_REPORT_NAMES.has(event.name)).sort((a, b) => b.ts.localeCompare(a.ts))),
      )
      .catch(() => undefined)
  }

  useEffect(reload, [ticketId])

  function handleDecide(eventId: number, approved: boolean) {
    decide(eventId, approved).then(() => {
      reload()
      onChanged()
    })
  }

  function handleRequestChanges(eventId: number, comment: string) {
    requestChanges(eventId, comment).then(() => {
      reload()
      onChanged()
    })
  }

  if (events.length === 0) return null

  return (
    <VStack gap={2}>
      <Heading level={4}>Plans & reports</Heading>
      <VStack gap={1}>
        {events.map((event) => {
          const style = NAME_STYLE[event.name]
          return (
            <button
              key={event.id}
              type="button"
              onClick={() => setSelected(event)}
              style={{ all: 'unset', cursor: 'pointer', display: 'block', width: '100%' }}
            >
              <HStack
                gap={2}
                align="center"
                style={{
                  padding: '6px 10px',
                  borderInlineStart: style ? `3px solid ${style.fg}` : undefined,
                  background: 'var(--color-background-muted)',
                  borderRadius: 'var(--radius-inner)',
                }}
              >
                <StatusIcon status={event.status} name={event.name} size={28} />
                <Text type="body" size="sm" weight="semibold" style={{ flex: 1 }}>
                  {displayEventName(event.name)}
                </Text>
                <Text type="body" size="sm" color="secondary">
                  {formatOverviewTime(event.ts)}
                </Text>
              </HStack>
            </button>
          )
        })}
      </VStack>
      <EventDetailDialog
        event={selected}
        onClose={() => setSelected(null)}
        onDecide={selected ? (approved) => handleDecide(selected.id, approved) : undefined}
        onRequestChanges={selected ? (comment) => handleRequestChanges(selected.id, comment) : undefined}
      />
    </VStack>
  )
}

// The dialog body -- a separate component (rather than inline in
// TicketDetailDialog) so its rename-editing state can be hooks-legal: it
// only ever mounts once `ticket` is known non-null, the same guarantee the
// old ticket-only TitleField subcomponent relied on.
//
// The ticket's own title sits in the dialog header itself (in place of a
// generic "Ticket details" label) with the rename pencil beside it in
// `endContent`; DialogHeader's title is plain text, so the inline-rename
// TextInput renders in the body instead, appearing only while editing.
function TicketDetailBody({
  ticket,
  onClose,
  onChanged,
}: {
  ticket: TicketOut
  onClose: () => void
  onChanged: () => void
}) {
  const navigate = useNavigate()
  const [isEditingTitle, setIsEditingTitle] = useState(false)
  const [draft, setDraft] = useState(ticket.title)

  useEffect(() => {
    setDraft(ticket.title)
    setIsEditingTitle(false)
  }, [ticket.id, ticket.title])

  function cancelRename() {
    setDraft(ticket.title)
    setIsEditingTitle(false)
  }

  async function saveRename() {
    const trimmed = draft.trim()
    if (!trimmed || trimmed === ticket.title) {
      cancelRename()
      return
    }
    await api.updateTicket(ticket.id, { title: trimmed })
    setIsEditingTitle(false)
    onChanged()
  }

  async function moveToTrash() {
    await api.trashTicket(ticket.id)
    onChanged()
    onClose()
  }

  function viewActivityLog() {
    onClose()
    navigate(`/logs?ticket=${ticket.id}`)
  }

  return (
    <Layout
      header={
        <DialogHeader
          title={ticket.title}
          subtitle={`#${ticket.id}`}
          onOpenChange={onClose}
          endContent={
            <Button
              label="Rename ticket"
              isIconOnly
              size="sm"
              variant="ghost"
              icon={<PencilIcon style={{ width: 14, height: 14 }} />}
              onClick={() => setIsEditingTitle(true)}
            />
          }
        />
      }
      content={
        <LayoutContent isScrollable>
          <VStack gap={4}>
            {isEditingTitle && (
              <HStack gap={2} align="center">
                <TextInput
                  label="Title"
                  isLabelHidden
                  value={draft}
                  onChange={setDraft}
                  hasAutoFocus
                  onEnter={() => void saveRename()}
                  onKeyDown={(event) => {
                    if (event.key === 'Escape') cancelRename()
                  }}
                  width={340}
                />
                <Button
                  label="Save"
                  isIconOnly
                  size="sm"
                  variant="ghost"
                  icon={<CheckIcon style={{ width: 14, height: 14 }} />}
                  onClick={() => void saveRename()}
                />
                <Button
                  label="Cancel rename"
                  isIconOnly
                  size="sm"
                  variant="ghost"
                  icon={<XMarkIcon style={{ width: 14, height: 14 }} />}
                  onClick={cancelRename}
                />
              </HStack>
            )}

            <HStack gap={3} align="center" wrap="wrap">
              <Badge
                variant={STATE_BADGE_VARIANT[ticket.state] ?? 'neutral'}
                icon={(() => {
                  const Icon = STATE_ICON[ticket.state]
                  return Icon ? <Icon style={{ width: 12, height: 12 }} /> : undefined
                })()}
                label={STATE_LABEL[ticket.state] ?? ticket.state}
              />
              <MetaItem icon={CalendarIcon}>{formatFullTime(ticket.created_at)}</MetaItem>
              {ticket.resource_id && <MetaItem icon={MapPinIcon}>{ticket.resource_id}</MetaItem>}
            </HStack>

            {ticket.latest_event_name && (
              <VStack gap={1}>
                <Text type="body" weight="bold" size="sm">
                  Latest activity
                </Text>
                <Text type="body" size="sm">
                  {describeLatestAction(ticket.latest_event_name, ticket.latest_event_status ?? 'success')}
                  {ticket.latest_event_ts && (
                    <Text as="span" type="body" size="sm" color="secondary">
                      {' '}
                      · {formatOverviewTime(ticket.latest_event_ts)}
                    </Text>
                  )}
                </Text>
                {ticket.latest_event_action && <CodeInline>{ticket.latest_event_action}</CodeInline>}
              </VStack>
            )}

            <HStack gap={1} align="center">
              <ChatBubbleLeftIcon style={{ width: 14, height: 14, color: 'var(--color-icon-secondary)' }} />
              <Text type="body" size="sm" color="secondary">
                {ticket.comment_count} comment{ticket.comment_count === 1 ? '' : 's'}
              </Text>
            </HStack>

            <VStack gap={2}>
              <Heading level={4}>Started from</Heading>
              <div
                style={{
                  background: 'var(--color-background-muted)',
                  borderRadius: 'var(--radius-inner)',
                  padding: '10px 12px',
                }}
              >
                <Text
                  type="body"
                  size="sm"
                  color={ticket.initial_prompt ? undefined : 'secondary'}
                  style={{ whiteSpace: 'pre-wrap' }}
                >
                  {ticket.initial_prompt || '(no prompt captured)'}
                </Text>
              </div>
            </VStack>

            <PlanReportHistory ticketId={ticket.id} onChanged={onChanged} />

            <HStack gap={2} style={{ justifyContent: 'flex-end' }}>
              <Button
                label="View full activity log"
                variant="secondary"
                icon={<ArrowTopRightOnSquareIcon style={{ width: 14, height: 14 }} />}
                onClick={viewActivityLog}
              />
              <Button
                label="Move to trash"
                variant="destructive"
                icon={<TrashIcon style={{ width: 14, height: 14 }} />}
                onClick={() => void moveToTrash()}
              />
            </HStack>
          </VStack>
        </LayoutContent>
      }
    />
  )
}

// The shared ticket-detail popup -- opened from a card on the Dashboard's
// Kanban board or a row on the Tickets page, same component either way.
// `initial_prompt` (the verbatim request that opened this investigation) is
// shown read-only, since it's a fixed historical record, not a display
// name. "Move to trash" is reversible (no confirmation) -- permanent delete
// lives in the Tickets page's Trash section instead. "View full activity
// log" keeps the previous click-a-card behavior (deep-linking into Logs)
// alive as a secondary action rather than dropping it.
export function TicketDetailDialog({
  ticket,
  onClose,
  onChanged,
}: {
  ticket: TicketOut | null
  onClose: () => void
  onChanged: () => void
}) {
  return (
    <Dialog isOpen={ticket !== null} onOpenChange={(open) => !open && onClose()} width={560} purpose="info">
      {ticket && <TicketDetailBody ticket={ticket} onClose={onClose} onChanged={onChanged} />}
    </Dialog>
  )
}
