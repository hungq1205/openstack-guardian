import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { FunnelIcon, MagnifyingGlassIcon, TrashIcon } from '@heroicons/react/24/outline'
import { Button } from '@astryxdesign/core/Button'
import { Selector, type SelectorOptionData } from '@astryxdesign/core/Selector'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Switch } from '@astryxdesign/core/Switch'
import { Heading, Text } from '@astryxdesign/core/Text'
import { TextInput } from '@astryxdesign/core/TextInput'
import { DateTimeField } from '../components/DateTimeField'
import { EventDetailDialog } from '../components/EventDetailDialog'
import { EventTimeline } from '../components/EventTimeline'
import { api, type EventOut, type TicketOut } from '../lib/api'
import { isAutoOpenEvent } from '../lib/eventDisplay'
import { usePendingApprovals } from '../lib/pendingApprovals'
import { localInputValueToUtcIso } from '../lib/time'
import { STATE_LABEL } from '../lib/ticketState'
import { useToolCatalog } from '../lib/toolCatalog'

const SERVER_OPTIONS = ['', 'cmp-admin', 'cmp-logs', 'cmp-notify']
const KIND_OPTIONS = ['', 'tool', 'resource', 'prompt']
const STATUS_OPTIONS = ['', 'success', 'error']
const TICKET_STATE_OPTIONS: SelectorOptionData[] = [
  { value: '', label: 'Any ticket state' },
  ...Object.entries(STATE_LABEL).map(([value, label]) => ({ value, label })),
]
const MAX_LIVE_EVENTS = 500

function matchesTicketFilter(ticket: string, eventTicketId: number | null): boolean {
  if (!ticket) return true
  if (ticket === 'unassigned') return eventTicketId === null
  return String(eventTicketId) === ticket
}

// Live-tail events are matched client-side against a state filter (the
// backend's own `ticket_state` filter only applies to the initial/refresh
// fetch) by looking up each event's ticket in the same list already fetched
// for the Ticket selector -- no separate lookup, no schema change.
function matchesTicketStateFilter(
  ticketState: string,
  eventTicketId: number | null,
  ticketStatesById: Map<number, string>,
): boolean {
  if (!ticketState) return true
  return eventTicketId !== null && ticketStatesById.get(eventTicketId) === ticketState
}

export function LogsPage() {
  const [searchParams] = useSearchParams()
  const [search, setSearch] = useState('')
  const [server, setServer] = useState('')
  const [kind, setKind] = useState('')
  const [status, setStatus] = useState('')
  const [ticket, setTicket] = useState(() => searchParams.get('ticket') ?? '')
  const [ticketState, setTicketState] = useState(() => searchParams.get('state') ?? '')
  const [tickets, setTickets] = useState<TicketOut[]>([])
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const [isFilterOpen, setIsFilterOpen] = useState(false)
  const [events, setEvents] = useState<EventOut[]>([])
  const { descriptions, toolKind } = useToolCatalog()
  const { decide, requestChanges } = usePendingApprovals()
  const [selected, setSelected] = useState<EventOut | null>(null)
  const [isLive, setIsLive] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [isClearing, setIsClearing] = useState(false)
  const eventSourceRef = useRef<EventSource | null>(null)

  useEffect(reloadTickets, [])

  const ticketOptions: SelectorOptionData[] = useMemo(
    () => [
      { value: '', label: 'All tickets' },
      { value: 'unassigned', label: 'Unassigned' },
      ...tickets.map((t) => ({ value: String(t.id), label: t.title })),
    ],
    [tickets],
  )

  const activeFilterCount = [server, kind, status, ticket, ticketState, since, until].filter(Boolean).length

  const ticketStatesById = useMemo(() => new Map(tickets.map((t) => [t.id, t.state])), [tickets])
  const ticketStatesByIdRef = useRef(ticketStatesById)
  useEffect(() => {
    ticketStatesByIdRef.current = ticketStatesById
  }, [ticketStatesById])

  // decide() (from PendingApprovalsProvider) updates the shared pending list
  // live -- that's what makes the toast/Dashboard panel disappear instantly.
  // This page's own `events` array is a separate fetch, though, so without
  // this it wouldn't reflect the new status until "Live tail" is on or
  // Refresh is clicked.
  function reloadTickets() {
    api.listTickets().then(setTickets).catch(() => undefined)
  }

  function handleDecide(eventId: number, approved: boolean) {
    decide(eventId, approved).then(reloadTickets)
    setEvents((current) =>
      current.map((event) => (event.id === eventId ? { ...event, status: approved ? 'approved' : 'denied' } : event)),
    )
  }

  function handleRequestChanges(eventId: number, comment: string) {
    requestChanges(eventId, comment).then(reloadTickets)
    setEvents((current) =>
      current.map((event) =>
        event.id === eventId ? { ...event, status: 'changes_requested', error_message: comment } : event,
      ),
    )
  }

  function reload() {
    setError(null)
    api
      .listEvents({
        server: server || undefined,
        kind: kind || undefined,
        status: status || undefined,
        ticket_id: ticket || undefined,
        ticket_state: ticketState || undefined,
        since: localInputValueToUtcIso(since),
        until: localInputValueToUtcIso(until),
        limit: 200,
      })
      .then(setEvents)
      .catch((err: Error) => setError(err.message))
  }

  useEffect(reload, [server, kind, status, ticket, ticketState, since, until])

  useEffect(() => {
    if (!isLive) {
      eventSourceRef.current?.close()
      eventSourceRef.current = null
      return
    }
    const source = new EventSource('/api/events/stream')
    eventSourceRef.current = source
    source.addEventListener('log', (message) => {
      const event = JSON.parse((message as MessageEvent<string>).data) as EventOut
      const matchesFilters =
        (!server || event.server === server) &&
        (!kind || event.kind === kind) &&
        (!status || event.status === status) &&
        matchesTicketFilter(ticket, event.ticket_id) &&
        matchesTicketStateFilter(ticketState, event.ticket_id, ticketStatesByIdRef.current)
      setEvents((current) => {
        const existingIndex = current.findIndex((e) => e.id === event.id)
        if (!matchesFilters) {
          return existingIndex === -1 ? current : current.filter((e) => e.id !== event.id)
        }
        if (existingIndex !== -1) {
          // A pending row re-emitted with a new status (approved/denied/
          // success/error) updates in place -- it must not jump back to the
          // top of a live-tailing list every ~1.5s just because it's still
          // pending.
          const next = [...current]
          next[existingIndex] = event
          return next
        }
        if (isAutoOpenEvent(event.name)) {
          setSelected(event)
        }
        return [event, ...current].slice(0, MAX_LIVE_EVENTS)
      })
    })
    return () => source.close()
  }, [isLive, server, kind, status, ticket, ticketState])

  const visibleEvents = useMemo(() => {
    const needle = search.trim().toLowerCase()
    if (!needle) return events
    return events.filter((event) =>
      [event.name, event.action ?? '', event.server, event.kind].some((field) =>
        field.toLowerCase().includes(needle),
      ),
    )
  }, [events, search])

  function clearFilters() {
    setServer('')
    setKind('')
    setStatus('')
    setTicket('')
    setTicketState('')
    setSince('')
    setUntil('')
  }

  function clearLogs() {
    if (!window.confirm('Delete every logged event? This cannot be undone.')) return
    setIsClearing(true)
    setError(null)
    api
      .clearEvents()
      .then(() => setEvents([]))
      .catch((err: Error) => setError(err.message))
      .finally(() => setIsClearing(false))
  }

  return (
    <VStack gap={4}>
      <VStack gap={1}>
        <Heading level={1}>Logs</Heading>
      </VStack>

      <VStack gap={3}>
        <HStack gap={3} align="center" wrap="wrap">
          <TextInput
            label="Search"
            isLabelHidden
            startIcon={MagnifyingGlassIcon}
            hasClear
            placeholder="Search activity..."
            value={search}
            onChange={setSearch}
            width={260}
          />
          <Button
            label={`Filters${activeFilterCount ? ` (${activeFilterCount})` : ''}`}
            icon={<FunnelIcon style={{ width: 16, height: 16 }} />}
            variant={isFilterOpen ? 'primary' : 'secondary'}
            onClick={() => setIsFilterOpen((open) => !open)}
          />
          <Switch label="Live tail" value={isLive} onChange={setIsLive} />
          <div style={{ flexGrow: 1 }}></div>
          <Button
            label="Clear logs"
            icon={<TrashIcon style={{ width: 16, height: 16 }} />}
            variant="ghost"
            isDisabled={isClearing || events.length === 0}
            onClick={clearLogs}
          />
          <Button label="Refresh" onClick={reload} variant="secondary" />
        </HStack>

        {isFilterOpen && (
          <div
            style={{
              padding: 16,
              border: '1px solid var(--color-border)',
              borderRadius: 'var(--radius-container)',
              background: 'var(--color-background-card)',
            }}
          >
            <HStack gap={3} align="end" wrap="wrap">
              <Selector label="Server" options={SERVER_OPTIONS} value={server} onChange={(v) => setServer(v ?? '')} />
              <Selector label="Kind" options={KIND_OPTIONS} value={kind} onChange={(v) => setKind(v ?? '')} />
              <Selector label="Status" options={STATUS_OPTIONS} value={status} onChange={(v) => setStatus(v ?? '')} />
              <Selector label="Ticket" options={ticketOptions} value={ticket} onChange={(v) => setTicket(v ?? '')} />
              <Selector
                label="Ticket state"
                options={TICKET_STATE_OPTIONS}
                value={ticketState}
                onChange={(v) => setTicketState(v ?? '')}
              />
              <DateTimeField label="From" value={since} onChange={setSince} />
              <DateTimeField label="To" value={until} onChange={setUntil} />
              {activeFilterCount > 0 && <Button label="Clear filters" variant="ghost" onClick={clearFilters} />}
            </HStack>
          </div>
        )}
      </VStack>

      {error && <Text type="body">Failed to load events: {error}</Text>}

      <EventTimeline
        events={visibleEvents}
        onSelect={setSelected}
        descriptions={descriptions}
        toolKind={toolKind}
        onDecide={handleDecide}
        emptyLabel="No events match these filters."
      />

      <EventDetailDialog
        event={selected}
        onClose={() => setSelected(null)}
        onDecide={selected ? (approved) => handleDecide(selected.id, approved) : undefined}
        onRequestChanges={selected ? (comment) => handleRequestChanges(selected.id, comment) : undefined}
      />
    </VStack>
  )
}
