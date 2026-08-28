import { useState } from 'react'
import { ChatBubbleLeftIcon, CheckIcon, ChevronRightIcon, XMarkIcon } from '@heroicons/react/24/outline'
import { Button } from '@astryxdesign/core/Button'
import { HStack } from '@astryxdesign/core/Stack'
import { Text } from '@astryxdesign/core/Text'
import type { EventOut } from '../lib/api'
import { catalogDescriptionFor, displayEventName } from '../lib/eventDisplay'
import { formatDayLabel, formatDuration, formatTimeOnly } from '../lib/time'
import { CommentsDialog } from './CommentsDialog'
import { StatusIcon } from './StatusIcon'

// The small "N comments" indicator shown at the right of a row -- only when
// there's actually at least one, so an ordinary call with none stays clean.
// Interactive on a single event's own row (opens its comment thread);
// display-only on a collapsed read-group's aggregate, since there's no one
// event's thread to open for a sum across several.
function CommentIndicator({ count, onClick }: { count: number; onClick?: () => void }) {
  if (count === 0) return null
  return (
    <HStack
      gap={1}
      align="center"
      style={{ flexShrink: 0, cursor: onClick ? 'pointer' : 'default' }}
      onClick={
        onClick
          ? (event) => {
              event.stopPropagation()
              onClick()
            }
          : undefined
      }
    >
      <ChatBubbleLeftIcon style={{ width: 14, height: 14, color: 'var(--color-icon-secondary)' }} />
      <Text type="body" size="sm" color="secondary">
        {count}
      </Text>
    </HStack>
  )
}

// First argument of a call, formatted as a short trailing clause -- real
// data, not a fabricated detail, mirroring how the reference's entries end
// with a concrete specific ("serial LF-4829", "Condition: good").
const ARGUMENT_KEY_LABELS: Record<string, string> = {
  resource_id: 'server',
}

function firstArgumentPreview(argumentsJson: string): string | null {
  try {
    const parsed: unknown = JSON.parse(argumentsJson)
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) return null
    const entries = Object.entries(parsed as Record<string, unknown>)
    if (entries.length === 0) return null
    const [key, value] = entries[0]
    const rendered = typeof value === 'string' ? value : JSON.stringify(value)
    return `${ARGUMENT_KEY_LABELS[key] ?? key} ${rendered}`
  } catch {
    return null
  }
}

// Tool/operation descriptions are written for an agent, not a skimmable UI --
// often a full paragraph. Cut to the first sentence (or a hard character
// cap) so the timeline reads like the reference's terse lead-ins; the full
// text is still available where it's actually needed (Catalog's tooltip).
function leadSentence(text: string): string {
  const period = text.indexOf('. ')
  if (period !== -1 && period < 100) return text.slice(0, period + 1)
  if (text.length <= 90) return text
  return `${text.slice(0, 87).trimEnd()}…`
}

// A call reads if the catalog says so -- cmp-admin's curated tool_category,
// or the MCP readOnlyHint annotation for cmp-logs/cmp-notify (passed in via
// `toolKind`, keyed by tool name). A name absent from both (cmp-admin's own
// meta-tools, e.g. search_tools/get_tool_schema) falls back to the HTTP verb
// captured in `action`; a call with no action at all (purely local) is
// treated as a read since it has no side effect to flag.
function classifyEvent(event: EventOut, toolKind: Record<string, 'read' | 'action'>): 'read' | 'action' {
  if (event.kind !== 'tool') return 'read'
  const known = toolKind[event.name]
  if (known) return known
  if (event.action) {
    const verb = event.action.split(' ')[0]?.toUpperCase()
    return verb === 'GET' || verb === 'HEAD' ? 'read' : 'action'
  }
  return 'read'
}

type TimelineRow = { kind: 'single'; event: EventOut } | { kind: 'group'; events: EventOut[] }

// Every run of reads sitting between the actions that actually changed
// something collapses to one expandable line, even a run of one -- actions
// always stay fully visible, reads never need to.
function groupRows(events: EventOut[], toolKind: Record<string, 'read' | 'action'>): TimelineRow[] {
  const rows: TimelineRow[] = []
  let buffer: EventOut[] = []
  const flush = () => {
    if (buffer.length > 0) rows.push({ kind: 'group', events: buffer })
    buffer = []
  }
  for (const event of events) {
    if (classifyEvent(event, toolKind) === 'read') {
      buffer.push(event)
    } else {
      flush()
      rows.push({ kind: 'single', event })
    }
  }
  flush()
  return rows
}

function EventRow({
  event,
  onSelect,
  descriptions,
  onDecide,
  onOpenComments,
}: {
  event: EventOut
  onSelect: (event: EventOut) => void
  descriptions: Record<string, string>
  onDecide?: (eventId: number, approved: boolean) => void
  onOpenComments: (event: EventOut) => void
}) {
  const lead = catalogDescriptionFor(event.name, descriptions)
  const detail = firstArgumentPreview(event.arguments_json)
  return (
    <div
      onClick={() => onSelect(event)}
      style={{ display: 'flex', alignItems: 'flex-start', gap: 12, padding: '10px 4px', cursor: 'pointer' }}
    >
      <Text
        type="body"
        color="secondary"
        style={{ fontFamily: 'var(--font-family-code)', fontSize: 12, width: 48, flexShrink: 0, paddingTop: 6 }}
      >
        {formatTimeOnly(event.ts)}
      </Text>
      <StatusIcon status={event.status} name={event.name} />
      <div style={{ flex: 1, minWidth: 0, paddingTop: 4 }}>
        <Text type="body">
          {lead ? `${leadSentence(lead)} — ` : ''}
          <span style={{ fontWeight: 700 }}>{displayEventName(event.name)}</span>
          {detail ? `, ${detail}` : ''}
        </Text>
        {event.action && (
          <Text
            type="body"
            color="secondary"
            style={{ fontFamily: 'var(--font-family-code)', fontSize: 12, display: 'block', marginTop: 2 }}
          >
            {event.action}
          </Text>
        )}
        {event.status === 'error' && event.error_message && (
          <div
            style={{
              marginTop: 6,
              padding: '8px 12px',
              border: '1px solid var(--color-border)',
              borderInlineStartWidth: 3,
              borderInlineStartColor: 'var(--color-border-red)',
              borderInlineStartStyle: 'solid',
              borderRadius: 'var(--radius-container)',
              background: 'var(--color-background-card)',
              maxWidth: 560,
            }}
          >
            <Text type="body" size="sm" style={{ color: 'var(--color-text-red)' }}>
              {event.error_message}
            </Text>
          </div>
        )}
      </div>
      <div style={{ display: 'flex', flexDirection: 'row', alignItems: 'center', gap: 8, flexShrink: 0, paddingTop: 6 }}>
        <CommentIndicator count={event.comment_count} onClick={() => onOpenComments(event)} />
        {event.status === 'pending' && onDecide ? (
          <HStack gap={1} onClick={(e) => e.stopPropagation()}>
            <Button
              label="Approve"
              isIconOnly
              size="sm"
              variant="primary"
              icon={<CheckIcon style={{ width: 14, height: 14 }} />}
              onClick={() => onDecide(event.id, true)}
            />
            <Button
              label="Deny"
              isIconOnly
              size="sm"
              variant="destructive"
              icon={<XMarkIcon style={{ width: 14, height: 14 }} />}
              onClick={() => onDecide(event.id, false)}
            />
          </HStack>
        ) : (
          <Text
            type="body"
            size="sm"
            color="secondary"
            style={{ fontVariantNumeric: 'tabular-nums', width: 44, textAlign: 'right' }}
          >
            {formatDuration(event.duration_ms)}
          </Text>
        )}
      </div>
    </div>
  )
}

// The collapsed summary for a run of consecutive reads: same column layout
// as a normal row (time / icon slot / content / duration) so it stays
// visually aligned with the rows above and below it, just with a chevron
// standing in for the status icon and the individual calls tucked away.
function ReadGroupRow({
  events,
  isOpen,
  onToggle,
  onSelect,
  descriptions,
  onOpenComments,
}: {
  events: EventOut[]
  isOpen: boolean
  onToggle: () => void
  onSelect: (event: EventOut) => void
  descriptions: Record<string, string>
  onOpenComments: (event: EventOut) => void
}) {
  const totalDuration = events.reduce((sum, event) => sum + event.duration_ms, 0)
  // Comments don't disappear just because their event is tucked into a
  // collapsed group -- summed here so the group row still signals there's
  // something to see, even before it's expanded.
  const totalComments = events.reduce((sum, event) => sum + event.comment_count, 0)
  return (
    <div>
      <div
        onClick={onToggle}
        style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 4px', cursor: 'pointer', opacity: 0.8 }}
      >
        <div style={{ width: 48, flexShrink: 0 }} />
        <div style={{ width: 32, height: 24, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <ChevronRightIcon
            style={{
              width: 14,
              height: 14,
              color: 'var(--color-icon-secondary)',
              transform: isOpen ? 'rotate(90deg)' : 'none',
              transition: 'transform 120ms ease',
            }}
          />
        </div>
        <Text type="body" color="secondary" size="sm" style={{ flex: 1 }}>
          Reading tools · {events.length}
        </Text>
        <CommentIndicator count={totalComments} />
        <Text
          type="body"
          color="secondary"
          size="sm"
          style={{ flexShrink: 0, fontVariantNumeric: 'tabular-nums', width: 44, textAlign: 'right' }}
        >
          {formatDuration(totalDuration)}
        </Text>
      </div>
      {isOpen && (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            marginInlineStart: 60,
            paddingInlineStart: 12,
            borderInlineStart: '2px solid var(--color-border)',
          }}
        >
          {events.map((event) => (
            <EventRow
              key={event.id}
              event={event}
              onSelect={onSelect}
              descriptions={descriptions}
              onOpenComments={onOpenComments}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// A day-grouped activity feed read as sentences, not a data table: a status
// icon, a plain-language lead (the tool's own catalog description) followed
// by the bold call name and a concrete detail, with the raw action and any
// error tucked underneath. Every read call -- one or many, wherever it falls
// between the actions that actually did something -- collapses into one
// expandable line, so a scan of the page shows what changed, not every
// lookup along the way.
export function EventTimeline({
  events,
  onSelect,
  descriptions,
  toolKind,
  onDecide,
  emptyLabel = 'No activity recorded yet.',
}: {
  events: EventOut[]
  onSelect: (event: EventOut) => void
  descriptions: Record<string, string>
  toolKind: Record<string, 'read' | 'action'>
  onDecide?: (eventId: number, approved: boolean) => void
  emptyLabel?: string
}) {
  const [openGroups, setOpenGroups] = useState<Set<number>>(new Set())
  const [commentsEvent, setCommentsEvent] = useState<EventOut | null>(null)

  function toggleGroup(id: number) {
    setOpenGroups((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  if (events.length === 0) {
    return (
      <Text type="body" color="secondary">
        {emptyLabel}
      </Text>
    )
  }

  const groups: { label: string; items: EventOut[] }[] = []
  for (const event of events) {
    const label = formatDayLabel(event.ts)
    const lastGroup = groups[groups.length - 1]
    if (lastGroup && lastGroup.label === label) {
      lastGroup.items.push(event)
    } else {
      groups.push({ label, items: [event] })
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {groups.map((group) => (
        <div key={group.label} style={{ display: 'flex', flexDirection: 'column' }}>
          <Text
            type="label"
            size="xsm"
            color="secondary"
            style={{ textTransform: 'uppercase', letterSpacing: '0.06em', display: 'block', paddingBlock: '4px 8px' }}
          >
            {group.label}
          </Text>
          {groupRows(group.items, toolKind).map((row) =>
            row.kind === 'single' ? (
              <EventRow
                key={row.event.id}
                event={row.event}
                onSelect={onSelect}
                descriptions={descriptions}
                onDecide={onDecide}
                onOpenComments={setCommentsEvent}
              />
            ) : (
              // Keyed on the oldest event in the run, not the newest: `events` is
              // newest-first, and live tail prepends new reads onto the front of
              // this same top group as they arrive -- events[0] changes on every
              // new entry, but the oldest boundary never does, so this is the one
              // identity that survives the group growing and keeps `isOpen` attached
              // to the same group instead of resetting to closed.
              <ReadGroupRow
                key={row.events[row.events.length - 1].id}
                events={row.events}
                isOpen={openGroups.has(row.events[row.events.length - 1].id)}
                onToggle={() => toggleGroup(row.events[row.events.length - 1].id)}
                onSelect={onSelect}
                descriptions={descriptions}
                onOpenComments={setCommentsEvent}
              />
            ),
          )}
        </div>
      ))}
      <CommentsDialog
        eventId={commentsEvent?.id ?? null}
        eventLabel={commentsEvent ? displayEventName(commentsEvent.name) : ''}
        onClose={() => setCommentsEvent(null)}
        onChanged={() => undefined}
      />
    </div>
  )
}
