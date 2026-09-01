import { useMemo, useState, type CSSProperties } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowRightIcon,
  ArrowTopRightOnSquareIcon,
  ChevronDownIcon,
  CheckIcon,
  ChatBubbleLeftIcon,
  LinkIcon,
} from '@heroicons/react/24/outline'
import { CheckCircleIcon, ClipboardDocumentListIcon } from '@heroicons/react/24/solid'
import { Badge } from '@astryxdesign/core/Badge'
import { Button } from '@astryxdesign/core/Button'
import { Card } from '@astryxdesign/core/Card'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Text } from '@astryxdesign/core/Text'
import type { EventOut, TicketOut } from '../lib/api'
import { describeLatestAction } from '../lib/eventDisplay'
import { fetchLatestEvent } from '../lib/latestEvent'
import { formatOverviewTime } from '../lib/time'
import { STATE_BADGE_VARIANT, STATE_COLOR, STATE_ICON, STATE_LABEL } from '../lib/ticketState'

// Kanban column order, left to right -- `completed` is deliberately not a
// column: a finished ticket needs nothing further from anyone, so it's
// demoted to a collapsed strip beneath the board instead of competing for
// space with tickets that do.
const KANBAN_COLUMNS = ['investigating', 'planned', 'resolving', 'in_review', 'escalated'] as const

// Deliberately smaller and more muted than the card's other actions -- these
// are secondary, low-frequency affordances (peek at comments, grab a link),
// not primary calls to action, so they shouldn't compete visually with the
// title or the delete control.
const MINOR_ACTION_STYLE = { height: 20, minHeight: 20, padding: '0 4px', color: 'var(--color-text-secondary)' }
const MINOR_ICON_STYLE = { width: 12, height: 12, color: 'var(--color-icon-secondary)' }

// -15px cancels the Card's own `padding={4}` (15px) so the notches sit
// exactly on the card's true edge, not its padded content edge.
const NOTCH: CSSProperties = {
  position: 'absolute',
  top: '50%',
  width: 24,
  height: 16,
  marginTop: -8,
  borderRadius: '50%',
  background: 'var(--color-background-body)',
  border: '1px solid var(--color-border)',
  zIndex: 100,
}

// A ticket-stub perforation: a row of small punched dots (not a CSS
// `dashed` border -- that reads as little rectangles, not a tear line)
// with a semicircle bitten out of each card edge. Each notch is clipped to
// just its inner half via `clip-path` on the notch itself, so only the arc
// facing into the card ever renders -- no ring floating past the edge.
function TicketPerforation() {
  return (
    <div style={{ position: 'relative', margin: '4px -15px 10px', height: 1 }}>
      <span style={{ ...NOTCH, left: -13.5, clipPath: 'inset(0 0 0 50%)' }} />
      <span style={{ ...NOTCH, right: -13.5, clipPath: 'inset(0 50% 0 0)' }} />
      <div
        style={{
          height: 3,
          margin: '-1px 15px 0',
          backgroundImage: 'radial-gradient(circle at center, var(--color-border) 1.3px, transparent 1.4px)',
          backgroundSize: '7px 3px',
          backgroundRepeat: 'repeat-x',
          backgroundPosition: 'center',
        }}
      />
    </div>
  )
}

export function TicketCard({
  ticket,
  onOpen,
  onSelectEvent,
}: {
  ticket: TicketOut
  onOpen: () => void
  onSelectEvent: (event: EventOut) => void
}) {
  const navigate = useNavigate()
  const [justCopied, setJustCopied] = useState(false)
  const [isOpeningEvent, setIsOpeningEvent] = useState(false)
  const caption = ticket.latest_event_name
    ? describeLatestAction(ticket.latest_event_name, ticket.latest_event_status ?? 'success')
    : 'No activity yet'

  async function openLatestEvent() {
    if (isOpeningEvent) return
    setIsOpeningEvent(true)
    try {
      const latest = await fetchLatestEvent(ticket.id)
      if (latest) onSelectEvent(latest)
    } finally {
      setIsOpeningEvent(false)
    }
  }

  function copyDeepLink() {
    const url = `${window.location.origin}/logs?ticket=${ticket.id}`
    navigator.clipboard
      .writeText(url)
      .then(() => {
        setJustCopied(true)
        setTimeout(() => setJustCopied(false), 1500)
      })
      .catch(() => undefined)
  }

  const stateColor = STATE_COLOR[ticket.state] ?? STATE_COLOR.investigating

  return (
    <Card
      elevation="none"
      padding={4}
      onClick={onOpen}
      style={{
        cursor: 'pointer',
        borderRadius: 'var(--radius-inner)',
        borderTop: `3px solid ${stateColor.fg}`,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'visible',
      }}
    >
      <VStack gap={3} style={{ flex: 1 }}>
        <VStack gap={1} style={{ minWidth: 0 }}>
          <Text
            type="body"
            weight="bold"
            style={{
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {ticket.title}
          </Text>
          <Text type="body" size="sm" color="secondary">
            {formatOverviewTime(ticket.created_at)}
            {ticket.resource_id ? ` · ${ticket.resource_id}` : ''}
          </Text>
        </VStack>

        <TicketPerforation />

        <VStack gap={2}>
          <button
            type="button"
            disabled={!ticket.latest_event_name}
            onClick={(event) => {
              event.stopPropagation()
              void openLatestEvent()
            }}
            style={{
              all: 'unset',
              display: 'block',
              width: '100%',
              cursor: ticket.latest_event_name ? 'pointer' : 'default',
            }}
          >
            <VStack gap={1}>
              <Text type="body" size="sm" weight="semibold">
                {caption}
              </Text>
              {ticket.latest_event_ts && (
                <Text type="body" size="sm" color="secondary">
                  {formatOverviewTime(ticket.latest_event_ts)}
                </Text>
              )}
              {ticket.latest_event_action && (
                <Text
                  type="body"
                  size="sm"
                  color="secondary"
                  style={{
                    fontFamily: 'var(--font-family-code)',
                    fontSize: 12,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {ticket.latest_event_action}
                </Text>
              )}
            </VStack>
          </button>
        </VStack>

        <HStack align="center" style={{ justifyContent: 'flex-end', marginTop: 'auto' }}>
          <HStack gap={1} align="center">
            <Button
              label="Comments"
              size="sm"
              variant="ghost"
              style={MINOR_ACTION_STYLE}
              icon={<ChatBubbleLeftIcon style={MINOR_ICON_STYLE} />}
              onClick={(event) => {
                event.stopPropagation()
                onOpen()
              }}
            >
              {ticket.comment_count}
            </Button>
            <Button
              label={justCopied ? 'Copied!' : 'Copy deep link'}
              isIconOnly
              size="sm"
              variant="ghost"
              style={{ ...MINOR_ACTION_STYLE, width: 20, padding: 0 }}
              icon={
                justCopied ? (
                  <CheckIcon style={{ ...MINOR_ICON_STYLE, color: 'var(--color-icon-green)' }} />
                ) : (
                  <LinkIcon style={MINOR_ICON_STYLE} />
                )
              }
              onClick={(event) => {
                event.stopPropagation()
                copyDeepLink()
              }}
            />
            <Button
              label="View logs"
              isIconOnly
              size="sm"
              variant="ghost"
              style={{ ...MINOR_ACTION_STYLE, width: 20, padding: 0 }}
              icon={<ArrowTopRightOnSquareIcon style={MINOR_ICON_STYLE} />}
              onClick={(event) => {
                event.stopPropagation()
                navigate(`/logs?ticket=${ticket.id}`)
              }}
            />
          </HStack>
        </HStack>
      </VStack>
    </Card>
  )
}

function sortByRecency(tickets: TicketOut[]): TicketOut[] {
  return tickets.slice().sort((a, b) => b.id - a.id)
}

// One column's header: a colored pill (icon + label + count), plus a
// forward button that jumps to the Logs page pre-filtered to every ticket
// currently in this column's state -- letting an operator go straight from
// "what's stuck in Resolving" to the actual call log behind it.
function ColumnHeader({ state, count }: { state: string; count: number }) {
  const navigate = useNavigate()
  const Icon = STATE_ICON[state] ?? ClipboardDocumentListIcon
  return (
    <HStack align="center" style={{ justifyContent: 'space-between' }}>
      <Badge
        variant={STATE_BADGE_VARIANT[state] ?? 'neutral'}
        icon={<Icon style={{ width: 12, height: 12 }} />}
        label={
          <>
            {STATE_LABEL[state] ?? state}
            <span style={{ opacity: 0.65, marginInlineStart: 5 }}>{count}</span>
          </>
        }
      />
      <Button
        label={`View ${STATE_LABEL[state] ?? state} logs`}
        isIconOnly
        size="sm"
        variant="ghost"
        style={{ color: 'var(--color-icon-secondary)' }}
        icon={<ArrowRightIcon style={{ width: 14, height: 14 }} />}
        onClick={() => navigate(`/logs?state=${state}`)}
      />
    </HStack>
  )
}

// One kanban column: header, then that state's cards stacked vertically.
function KanbanColumn({
  state,
  tickets,
  onOpen,
  onSelectEvent,
}: {
  state: string
  tickets: TicketOut[]
  onOpen: (ticket: TicketOut) => void
  onSelectEvent: (event: EventOut) => void
}) {
  return (
    <VStack gap={3} style={{ minWidth: 260, flex: '1 1 0' }}>
      <ColumnHeader state={state} count={tickets.length} />
      <VStack gap={3}>
        {tickets.length === 0 ? (
          <Text type="body" size="sm" color="secondary">
            No tickets
          </Text>
        ) : (
          sortByRecency(tickets).map((ticket) => (
            <TicketCard key={ticket.id} ticket={ticket} onOpen={() => onOpen(ticket)} onSelectEvent={onSelectEvent} />
          ))
        )}
      </VStack>
    </VStack>
  )
}

// Completed tickets sit below the board, collapsed by default -- a closed
// ticket needs nothing further from anyone, so it shouldn't cost the same
// visual weight as one still moving through the columns above.
function CompletedStrip({
  tickets,
  onOpen,
  onSelectEvent,
}: {
  tickets: TicketOut[]
  onOpen: (ticket: TicketOut) => void
  onSelectEvent: (event: EventOut) => void
}) {
  const [expanded, setExpanded] = useState(false)
  if (tickets.length === 0) return null
  return (
    <VStack gap={3} style={{ margin: '0 20px' }}>
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        style={{
          all: 'unset',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          gap: 6,
        }}
      >
        <Badge
          variant="neutral"
          icon={<CheckCircleIcon style={{ width: 12, height: 12 }} />}
          label={
            <>
              Completed<span style={{ opacity: 0.65, marginInlineStart: 5 }}>{tickets.length}</span>
            </>
          }
        />
        <ChevronDownIcon
          style={{
            width: 14,
            height: 14,
            color: 'var(--color-icon-secondary)',
            transform: expanded ? 'rotate(180deg)' : 'none',
          }}
        />
      </button>
      {expanded && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 40 }}>
          {sortByRecency(tickets).map((ticket) => (
            <TicketCard key={ticket.id} ticket={ticket} onOpen={() => onOpen(ticket)} onSelectEvent={onSelectEvent} />
          ))}
        </div>
      )}
    </VStack>
  )
}

// The Dashboard's ticket area: a kanban board, one column per non-terminal
// state in lifecycle order (KANBAN_COLUMNS), each holding that state's
// cards. Completed tickets are demoted to a collapsed strip below the
// board rather than a column of their own. Clicking any card opens the
// shared `TicketDetailDialog` (via `onOpenTicket`, owned by whichever page
// renders this grid); the "latest action" caption inside a card separately
// opens `EventDetailDialog` (via `onSelectEvent`) for whichever tool call a
// ticket last had, same as clicking a row on the Logs page.
export function TicketsGrid({
  tickets,
  onOpenTicket,
  onSelectEvent,
}: {
  tickets: TicketOut[]
  onOpenTicket: (ticket: TicketOut) => void
  onSelectEvent: (event: EventOut) => void
}) {
  const byState = useMemo(() => {
    const result: Record<string, TicketOut[]> = {}
    for (const ticket of tickets) (result[ticket.state] ??= []).push(ticket)
    return result
  }, [tickets])

  return (
    <VStack gap={5}>
      {tickets.length === 0 ? (
        <Text type="body" color="secondary">
          No tickets yet.
        </Text>
      ) : (
        <>
          <div style={{ display: 'flex', overflowX: 'auto', paddingBottom: 4 }}>
            {KANBAN_COLUMNS.map((state, index) => (
              <div
                key={state}
                style={{
                  flex: '1 1 0',
                  minWidth: 260,
                  padding: '0 20px',
                  borderRight: index < KANBAN_COLUMNS.length - 1 ? '1px dashed var(--color-border)' : 'none',
                }}
              >
                <KanbanColumn
                  state={state}
                  tickets={byState[state] ?? []}
                  onOpen={onOpenTicket}
                  onSelectEvent={onSelectEvent}
                />
              </div>
            ))}
          </div>
          <CompletedStrip tickets={byState.completed ?? []} onOpen={onOpenTicket} onSelectEvent={onSelectEvent} />
        </>
      )}
    </VStack>
  )
}
