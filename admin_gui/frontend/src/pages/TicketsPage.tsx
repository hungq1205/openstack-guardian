import { useEffect, useMemo, useState } from 'react'
import { ArrowUturnLeftIcon, ChevronDownIcon, FunnelIcon, MagnifyingGlassIcon, TrashIcon } from '@heroicons/react/24/outline'
import { Badge } from '@astryxdesign/core/Badge'
import { Button } from '@astryxdesign/core/Button'
import { Card } from '@astryxdesign/core/Card'
import { Selector, type SelectorOptionData } from '@astryxdesign/core/Selector'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Heading, Text } from '@astryxdesign/core/Text'
import { TextInput } from '@astryxdesign/core/TextInput'
import { DateTimeField } from '../components/DateTimeField'
import { EventDetailDialog } from '../components/EventDetailDialog'
import { TicketCard } from '../components/TicketsGrid'
import { TicketDetailDialog } from '../components/TicketDetailDialog'
import { TicketsTimeline } from '../components/TicketsTimeline'
import { api, type EventOut, type TicketOut } from '../lib/api'
import { usePendingApprovals } from '../lib/pendingApprovals'
import { localInputValueToUtcIso, formatDayLabel, formatOverviewTime } from '../lib/time'
import { STATE_BADGE_VARIANT, STATE_COLOR, STATE_LABEL } from '../lib/ticketState'

const STATE_OPTIONS: SelectorOptionData[] = [
  { value: '', label: 'All states' },
  ...Object.entries(STATE_LABEL).map(([value, label]) => ({ value, label })),
]

// A trashed ticket, shown with the same "upper part" as a live TicketCard --
// colored top border by state, title, resource/created meta, state badge --
// so the trash reads as a recognizable dimmed grid rather than a plain list.
// Not clickable (nothing pending to act on); the footer swaps the live
// card's comments/copy-link row for Restore / Delete permanently.
function TrashedTicketCard({
  ticket,
  onRestore,
  onDeletePermanently,
}: {
  ticket: TicketOut
  onRestore: () => void
  onDeletePermanently: () => void
}) {
  const stateColor = STATE_COLOR[ticket.state] ?? STATE_COLOR.investigating
  return (
    <Card
      elevation="none"
      padding={4}
      style={{
        borderRadius: 'var(--radius-inner)',
        borderTop: `3px solid ${stateColor.fg}`,
        opacity: 0.75,
      }}
    >
      <VStack gap={3}>
        <VStack gap={1} style={{ minWidth: 0 }}>
          <Text
            type="body"
            weight="bold"
            style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
          >
            {ticket.title}
          </Text>
          <Text type="body" size="sm" color="secondary">
            {formatOverviewTime(ticket.created_at)}
            {ticket.resource_id ? ` · ${ticket.resource_id}` : ''}
          </Text>
          <Badge variant={STATE_BADGE_VARIANT[ticket.state] ?? 'neutral'} label={STATE_LABEL[ticket.state] ?? ticket.state} />
        </VStack>

        <VStack gap={1}>
          <Text type="body" size="sm" color="secondary">
            {ticket.deleted_at ? `Trashed ${formatOverviewTime(ticket.deleted_at)}` : 'Trashed'}
          </Text>
          <HStack gap={2}>
            <Button
              label="Restore"
              size="sm"
              variant="ghost"
              icon={<ArrowUturnLeftIcon style={{ width: 14, height: 14 }} />}
              onClick={onRestore}
            />
            <Button
              label="Delete permanently"
              size="sm"
              variant="ghost"
              icon={<TrashIcon style={{ width: 14, height: 14 }} />}
              onClick={onDeletePermanently}
            />
          </HStack>
        </VStack>
      </VStack>
    </Card>
  )
}

// Trashed tickets, collapsed by default -- same expand/collapse shape as
// the Dashboard Kanban board's Completed strip. Restore is a single click
// (reversible action, matches trashing itself); permanent delete and "Empty
// trash" both confirm first, since those genuinely can't be undone, the
// same way Logs' "Clear logs" does.
function TrashSection({ tickets, onChanged }: { tickets: TicketOut[]; onChanged: () => void }) {
  const [expanded, setExpanded] = useState(false)
  const [isEmptying, setIsEmptying] = useState(false)

  async function restore(id: number) {
    await api.restoreTicket(id)
    onChanged()
  }

  async function deletePermanently(id: number) {
    if (!window.confirm('Permanently delete this ticket? This cannot be undone.')) return
    await api.deleteTicket(id)
    onChanged()
  }

  async function emptyTrash() {
    if (tickets.length === 0) return
    if (!window.confirm(`Permanently delete all ${tickets.length} ticket(s) in the trash? This cannot be undone.`)) {
      return
    }
    setIsEmptying(true)
    try {
      await Promise.all(tickets.map((ticket) => api.deleteTicket(ticket.id)))
      onChanged()
    } finally {
      setIsEmptying(false)
    }
  }

  if (tickets.length === 0) return null

  return (
    <VStack gap={3}>
      <HStack gap={3} align="center">
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6 }}
        >
          <Badge
            variant="neutral"
            icon={<TrashIcon style={{ width: 12, height: 12 }} />}
            label={
              <>
                Trash<span style={{ opacity: 0.65, marginInlineStart: 5 }}>{tickets.length}</span>
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
          <Button label="Empty trash" variant="ghost" size="sm" isDisabled={isEmptying} onClick={() => void emptyTrash()} />
        )}
      </HStack>
      {expanded && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 16 }}>
          {tickets.map((ticket) => (
            <TrashedTicketCard
              key={ticket.id}
              ticket={ticket}
              onRestore={() => void restore(ticket.id)}
              onDeletePermanently={() => void deletePermanently(ticket.id)}
            />
          ))}
        </div>
      )}
    </VStack>
  )
}

// The dedicated Tickets page: every non-trashed ticket in one flat,
// recency-sorted list (not grouped by state like the Dashboard's Kanban
// board), a timeline of state-change dots above it, a Logs-page-style
// filter bar, and a collapsed Trash section below. Opening a ticket (here
// or from the Dashboard) always shows the same `TicketDetailDialog`.
export function TicketsPage() {
  const [tickets, setTickets] = useState<TicketOut[]>([])
  const [trashedTickets, setTrashedTickets] = useState<TicketOut[]>([])
  const [selectedTicketId, setSelectedTicketId] = useState<number | null>(null)
  const [selectedEvent, setSelectedEvent] = useState<EventOut | null>(null)
  const [search, setSearch] = useState('')
  const [stateFilter, setStateFilter] = useState('')
  const [resourceFilter, setResourceFilter] = useState('')
  const [since, setSince] = useState('')
  const [until, setUntil] = useState('')
  const [highlightedTicketId, setHighlightedTicketId] = useState<number | null>(null)
  const [isFilterOpen, setIsFilterOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { decide, requestChanges } = usePendingApprovals()

  function reload() {
    api.listTickets().then(setTickets).catch((err: Error) => setError(err.message))
    api
      .listTickets({ trashed: true })
      .then(setTrashedTickets)
      .catch(() => undefined)
  }

  useEffect(reload, [])

  function handleDecide(eventId: number, approved: boolean) {
    decide(eventId, approved).then(reload)
  }

  function handleRequestChanges(eventId: number, comment: string) {
    requestChanges(eventId, comment).then(reload)
  }

  const resourceOptions: SelectorOptionData[] = useMemo(() => {
    const distinct = new Set<string>()
    for (const ticket of tickets) if (ticket.resource_id) distinct.add(ticket.resource_id)
    return [{ value: '', label: 'All resources' }, ...[...distinct].sort().map((id) => ({ value: id, label: id }))]
  }, [tickets])

  const activeFilterCount = [stateFilter, resourceFilter, since, until].filter(Boolean).length

  const visibleTickets = useMemo(() => {
    const needle = search.trim().toLowerCase()
    const sinceIso = localInputValueToUtcIso(since)
    const untilIso = localInputValueToUtcIso(until)
    return tickets
      .filter((t) => !stateFilter || t.state === stateFilter)
      .filter((t) => !resourceFilter || t.resource_id === resourceFilter)
      .filter((t) => highlightedTicketId === null || t.id === highlightedTicketId)
      .filter((t) => !needle || t.title.toLowerCase().includes(needle))
      .filter((t) => !sinceIso || t.created_at >= sinceIso)
      .filter((t) => !untilIso || t.created_at <= untilIso)
      .sort((a, b) => (b.latest_event_ts ?? b.created_at).localeCompare(a.latest_event_ts ?? a.created_at))
  }, [tickets, search, stateFilter, resourceFilter, since, until, highlightedTicketId])

  function clearFilters() {
    setStateFilter('')
    setResourceFilter('')
    setSince('')
    setUntil('')
    setHighlightedTicketId(null)
  }

  const groupedTickets = useMemo(() => {
    const groups: { label: string; items: TicketOut[] }[] = []
    for (const ticket of visibleTickets) {
      const label = formatDayLabel(ticket.latest_event_ts ?? ticket.created_at)
      const last = groups[groups.length - 1]
      if (last && last.label === label) last.items.push(ticket)
      else groups.push({ label, items: [ticket] })
    }
    return groups
  }, [visibleTickets])

  return (
    <VStack gap={5}>
      <Heading level={1}>Tickets</Heading>
      {error && <Text type="body">Failed to load tickets: {error}</Text>}

      <Card elevation="low">
        <VStack gap={3}>
          <Heading level={3}>Timeline</Heading>
          <TicketsTimeline highlightedTicketId={highlightedTicketId} onSelectTicket={setHighlightedTicketId} />
        </VStack>
      </Card>

      <VStack gap={3}>
        <HStack gap={3} align="center" wrap="wrap">
          <TextInput
            label="Search"
            isLabelHidden
            startIcon={MagnifyingGlassIcon}
            hasClear
            placeholder="Search tickets..."
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
              <Selector label="State" options={STATE_OPTIONS} value={stateFilter} onChange={(v) => setStateFilter(v ?? '')} />
              <Selector
                label="Resource"
                options={resourceOptions}
                value={resourceFilter}
                onChange={(v) => setResourceFilter(v ?? '')}
              />
              <DateTimeField label="From" value={since} onChange={setSince} />
              <DateTimeField label="To" value={until} onChange={setUntil} />
              {(activeFilterCount > 0 || highlightedTicketId !== null) && (
                <Button label="Clear filters" variant="ghost" onClick={clearFilters} />
              )}
            </HStack>
          </div>
        )}
      </VStack>

      {visibleTickets.length === 0 ? (
        <Text type="body" color="secondary">
          {tickets.length === 0 ? 'No tickets yet.' : 'No tickets match these filters.'}
        </Text>
      ) : (
        <VStack gap={5}>
          {groupedTickets.map((group) => (
            <VStack key={group.label} gap={3}>
              <Text
                type="label"
                size="xsm"
                color="secondary"
                style={{ textTransform: 'uppercase', letterSpacing: '0.06em' }}
              >
                {group.label}
              </Text>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 16 }}>
                {group.items.map((ticket) => (
                  <TicketCard
                    key={ticket.id}
                    ticket={ticket}
                    onOpen={() => setSelectedTicketId(ticket.id)}
                    onSelectEvent={setSelectedEvent}
                  />
                ))}
              </div>
            </VStack>
          ))}
        </VStack>
      )}

      <TrashSection tickets={trashedTickets} onChanged={reload} />

      <TicketDetailDialog
        ticket={[...tickets, ...trashedTickets].find((ticket) => ticket.id === selectedTicketId) ?? null}
        onClose={() => setSelectedTicketId(null)}
        onChanged={reload}
      />
      <EventDetailDialog
        event={selectedEvent}
        onClose={() => setSelectedEvent(null)}
        onDecide={selectedEvent ? (approved) => handleDecide(selectedEvent.id, approved) : undefined}
        onRequestChanges={selectedEvent ? (comment) => handleRequestChanges(selectedEvent.id, comment) : undefined}
      />
    </VStack>
  )
}
