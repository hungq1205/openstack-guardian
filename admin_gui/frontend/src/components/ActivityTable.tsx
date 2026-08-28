import { HStack } from '@astryxdesign/core/Stack'
import { proportional, Table } from '@astryxdesign/core/Table'
import { Text } from '@astryxdesign/core/Text'
import type { EventOut } from '../lib/api'
import { displayEventName } from '../lib/eventDisplay'
import { formatRelative } from '../lib/time'
import { ClickableCell } from './ClickableCell'
import { KindDot } from './KindIcon'

// The dashboard's flat, header-row activity feed -- modeled on the
// reference's Live Event Feed table, distinct from the day-grouped timeline
// used on the full Logs page. The per-row dot encodes kind (tool/resource/
// prompt), never status -- status stays the red bar on the right, on error
// only.
export function ActivityTable({
  events,
  onSelect,
}: {
  events: EventOut[]
  onSelect: (event: EventOut) => void
}) {
  return (
    <Table
      data={events}
      idKey="id"
      density="compact"
      isStriped
      hasHover
      columns={[
        {
          key: 'name',
          header: 'Event',
          width: proportional(2),
          renderCell: (row) => (
            <ClickableCell onClick={() => onSelect(row)}>
              <HStack gap={2} align="center">
                <KindDot kind={row.kind} />
                <Text type="body" weight="bold" style={{ fontFamily: 'var(--font-family-code)', fontSize: 13 }}>
                  {displayEventName(row.name)}
                </Text>
              </HStack>
            </ClickableCell>
          ),
        },
        {
          key: 'action',
          header: 'Detail',
          width: proportional(2),
          renderCell: (row) => (
            <ClickableCell onClick={() => onSelect(row)}>
              <Text
                type="body"
                color="secondary"
                style={{
                  fontFamily: 'var(--font-family-code)',
                  fontSize: 12,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {row.action ?? `${row.server} · ${row.kind}`}
              </Text>
            </ClickableCell>
          ),
        },
        {
          key: 'ts',
          header: 'Time',
          width: proportional(1),
          renderCell: (row) => (
            <ClickableCell onClick={() => onSelect(row)}>
              <Text type="body" color="secondary">
                {formatRelative(row.ts)}
              </Text>
            </ClickableCell>
          ),
        },
        {
          key: 'server',
          header: 'Source',
          width: proportional(1),
          renderCell: (row) => <ClickableCell onClick={() => onSelect(row)}>{row.server}</ClickableCell>,
        },
        {
          key: 'status',
          header: '',
          width: proportional(0.4),
          renderCell: (row) => (
            <ClickableCell onClick={() => onSelect(row)}>
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                {row.status === 'error' && (
                  <div style={{ width: 6, height: '1lh', background: 'var(--color-icon-red)' }} />
                )}
              </div>
            </ClickableCell>
          ),
        },
      ]}
    />
  )
}
