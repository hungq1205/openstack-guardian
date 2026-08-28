import { useEffect, useState } from 'react'
import { CheckIcon, XMarkIcon } from '@heroicons/react/24/outline'
import { Button } from '@astryxdesign/core/Button'
import { Card } from '@astryxdesign/core/Card'
import { Link } from '@astryxdesign/core/Link'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Heading, Text } from '@astryxdesign/core/Text'
import { ActivityTable } from '../components/ActivityTable'
import { EventDetailDialog } from '../components/EventDetailDialog'
import { TicketsGrid } from '../components/TicketsGrid'
import { api, type EventOut, type TicketOut } from '../lib/api'
import { catalogDescriptionFor, displayEventName } from '../lib/eventDisplay'
import { usePendingApprovals } from '../lib/pendingApprovals'
import { truncate } from '../lib/text'
import { useToolCatalog } from '../lib/toolCatalog'

const RECENT_SAMPLE_SIZE = 100

export function DashboardPage() {
  const [tickets, setTickets] = useState<TicketOut[]>([])
  const [events, setEvents] = useState<EventOut[]>([])
  const [selected, setSelected] = useState<EventOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { pendingEvents, decide, requestChanges } = usePendingApprovals()
  const { descriptions } = useToolCatalog()

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

      {pendingEvents.length > 0 && (
        <Card elevation="low" style={{ borderInlineStart: '3px solid var(--color-background-orange)' }}>
          <VStack gap={2}>
            <Heading level={3}>Pending approvals ({pendingEvents.length})</Heading>
            {pendingEvents.map((event) => (
              <HStack
                key={event.id}
                gap={4}
                align="center"
                style={{ padding: '10px 4px', borderBottom: '1px solid var(--color-border)' }}
              >
                <VStack gap={0} style={{ flex: 1, minWidth: 0 }}>
                  <Text type="body" weight="bold" style={{ fontFamily: 'var(--font-family-code)', fontSize: 13 }}>
                    {displayEventName(event.name)}
                  </Text>
                  <Text type="body" size="sm" color="secondary">
                    {event.server}
                    {catalogDescriptionFor(event.name, descriptions)
                      ? ` · ${truncate(catalogDescriptionFor(event.name, descriptions)!, 90)}`
                      : ''}
                  </Text>
                </VStack>
                <HStack gap={2}>
                  <Button
                    label="Approve"
                    size="sm"
                    variant="primary"
                    icon={<CheckIcon style={{ width: 14, height: 14 }} />}
                    onClick={() => handleDecide(event.id, true)}
                  />
                  <Button
                    label="Deny"
                    size="sm"
                    variant="destructive"
                    icon={<XMarkIcon style={{ width: 14, height: 14 }} />}
                    onClick={() => handleDecide(event.id, false)}
                  />
                </HStack>
              </HStack>
            ))}
          </VStack>
        </Card>
      )}

      <VStack gap={3}>
        <Heading level={3}>Tickets</Heading>
        <TicketsGrid tickets={tickets} onChanged={reloadTickets} />
      </VStack>

      <Card elevation="low">
        <VStack gap={3}>
          <HStack align="center" style={{ justifyContent: 'space-between' }}>
            <Heading level={3}>Recent activity</Heading>
            <Link href="/logs">View all</Link>
          </HStack>
          <ActivityTable events={events.slice(0, 12)} onSelect={setSelected} />
        </VStack>
      </Card>

      <EventDetailDialog
        event={selected}
        onClose={() => setSelected(null)}
        onDecide={selected ? (approved) => handleDecide(selected.id, approved) : undefined}
        onRequestChanges={selected ? (comment) => handleRequestChanges(selected.id, comment) : undefined}
      />
    </VStack>
  )
}
