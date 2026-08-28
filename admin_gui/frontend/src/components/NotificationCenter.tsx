import { useState } from 'react'
import { CheckIcon, XMarkIcon } from '@heroicons/react/24/outline'
import { Button } from '@astryxdesign/core/Button'
import { Card } from '@astryxdesign/core/Card'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Text } from '@astryxdesign/core/Text'
import type { EventOut } from '../lib/api'
import { catalogDescriptionFor, displayEventName } from '../lib/eventDisplay'
import { usePendingApprovals } from '../lib/pendingApprovals'
import { truncate } from '../lib/text'
import { useToolCatalog } from '../lib/toolCatalog'
import { EventDetailDialog } from './EventDetailDialog'

// The one place a pending approval is impossible to miss regardless of what
// page you're on: a toast stack pinned bottom-right, mounted once in Shell.
// Each toast approves/denies inline, or opens the same detail dialog Logs
// uses for full context before deciding.
export function NotificationCenter() {
  const { pendingEvents, decide } = usePendingApprovals()
  const { descriptions } = useToolCatalog()
  const [expanded, setExpanded] = useState<EventOut | null>(null)

  if (pendingEvents.length === 0) return null

  return (
    <>
      <div
        style={{
          position: 'fixed',
          bottom: 20,
          right: 20,
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
          width: 340,
          zIndex: 1000,
        }}
      >
        {pendingEvents.map((event) => {
          const description = catalogDescriptionFor(event.name, descriptions)
          return (
            <Card
              key={event.id}
              elevation="high"
              padding={3}
              onClick={() => setExpanded(event)}
              style={{
                cursor: 'pointer',
                borderInlineStart: '3px solid var(--color-background-orange)',
              }}
            >
              <VStack gap={2}>
                <VStack gap={0}>
                  <Text type="body" size="xsm" color="secondary">
                    Awaiting approval · {event.server}
                  </Text>
                  <Text type="body" weight="bold">
                    {displayEventName(event.name)}
                  </Text>
                  {description && (
                    <Text type="body" size="sm" color="secondary">
                      {truncate(description, 110)}
                    </Text>
                  )}
                </VStack>
                <HStack gap={2} onClick={(e) => e.stopPropagation()}>
                  <Button
                    label="Approve"
                    size="sm"
                    variant="primary"
                    icon={<CheckIcon style={{ width: 14, height: 14 }} />}
                    onClick={() => decide(event.id, true)}
                  />
                  <Button
                    label="Deny"
                    size="sm"
                    variant="destructive"
                    icon={<XMarkIcon style={{ width: 14, height: 14 }} />}
                    onClick={() => decide(event.id, false)}
                  />
                </HStack>
              </VStack>
            </Card>
          )
        })}
      </div>

      <EventDetailDialog
        event={expanded}
        onClose={() => setExpanded(null)}
        onDecide={
          expanded
            ? (approved) => {
                decide(expanded.id, approved)
                setExpanded(null)
              }
            : undefined
        }
      />
    </>
  )
}
