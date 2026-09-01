import { useEffect, useState } from 'react'
import { Card } from '@astryxdesign/core/Card'
import { Link } from '@astryxdesign/core/Link'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Heading, Text } from '@astryxdesign/core/Text'
import { EventDetailDialog } from '../components/EventDetailDialog'
import { classifyEvent, EventTimeline } from '../components/EventTimeline'
import { TicketDetailDialog } from '../components/TicketDetailDialog'
import { TicketsGrid } from '../components/TicketsGrid'
import { api, type EventOut, type TicketOut } from '../lib/api'
import { usePendingApprovals } from '../lib/pendingApprovals'
import { useToolCatalog } from '../lib/toolCatalog'

const RECENT_SAMPLE_SIZE = 100

export function DashboardPage() {
  const [tickets, setTickets] = useState<TicketOut[]>([])
  const [events, setEvents] = useState<EventOut[]>([])
  const [selected, setSelected] = useState<EventOut | null>(null)
  const [selectedTicketId, setSelectedTicketId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { decide, requestChanges } = usePendingApprovals()
  const { descriptions, toolKind } = useToolCatalog()

  function reloadTickets() {
    api.listTickets().then(setTickets).catch((err: Error) => setError(err.message))
  }

  useEffect(() => {
    reloadTickets()
    api
      .listEvents({ limit: RECENT_SAMPLE_SIZE })
      .then(setEvents)
      .catch(() => undefined)
  }, [])

  // decide() updates the shared pending list live (what makes the panel
  // above and the toast disappear instantly); this page's own `events`
  // array is a separate fetch and needs its own patch so a decision made
  // from the Recent activity dialog doesn't leave a stale pending row.
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

  return (
    <VStack gap={6}>
      <VStack gap={1}>
        <Heading level={1}>Dashboard</Heading>
      </VStack>
      {error && <Text type="body">Failed to load tickets: {error}</Text>}

      <VStack gap={3}>
        <Heading level={3}>Tickets</Heading>
        <TicketsGrid
          tickets={tickets}
          onOpenTicket={(ticket) => setSelectedTicketId(ticket.id)}
          onSelectEvent={setSelected}
        />
      </VStack>

      <Card elevation="low">
        <VStack gap={3}>
          <HStack align="center" style={{ justifyContent: 'space-between' }}>
            <Heading level={3}>Recent activity</Heading>
            <Link href="/logs">View all</Link>
          </HStack>
          <EventTimeline
            events={events.filter((event) => classifyEvent(event, toolKind) === 'action').slice(0, 12)}
            onSelect={setSelected}
            descriptions={descriptions}
            toolKind={toolKind}
            onDecide={handleDecide}
          />
        </VStack>
      </Card>

      <EventDetailDialog
        event={selected}
        onClose={() => setSelected(null)}
        onDecide={selected ? (approved) => handleDecide(selected.id, approved) : undefined}
        onRequestChanges={selected ? (comment) => handleRequestChanges(selected.id, comment) : undefined}
      />

      <TicketDetailDialog
        ticket={tickets.find((ticket) => ticket.id === selectedTicketId) ?? null}
        onClose={() => setSelectedTicketId(null)}
        onChanged={reloadTickets}
      />
    </VStack>
  )
}
