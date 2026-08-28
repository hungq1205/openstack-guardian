import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CheckIcon, ChatBubbleLeftIcon, LinkIcon, XMarkIcon } from '@heroicons/react/24/outline'
import {
  CheckCircleIcon,
  ClipboardDocumentListIcon,
  EyeIcon,
  MagnifyingGlassIcon,
  MegaphoneIcon,
  WrenchScrewdriverIcon,
} from '@heroicons/react/24/solid'
import { Button } from '@astryxdesign/core/Button'
import { Card } from '@astryxdesign/core/Card'
import { Dialog, DialogHeader } from '@astryxdesign/core/Dialog'
import { Layout, LayoutContent } from '@astryxdesign/core/Layout'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Heading, Text } from '@astryxdesign/core/Text'
import { api, type TicketOut } from '../lib/api'
import { describeLatestAction } from '../lib/eventDisplay'
import { formatRelative } from '../lib/time'

// Priority order for "smart" display -- whatever most needs an operator's
// attention floats to the top; a fully closed ticket needs nothing further
// from anyone, so it sinks to the very end regardless of how recent it is.
const STATE_ORDER = ['escalated', 'in_review', 'resolving', 'planned', 'investigating', 'completed'] as const

const STATE_LABEL: Record<string, string> = {
  investigating: 'Investigating',
  planned: 'Planned',
  resolving: 'Resolving',
  in_review: 'In Review',
  completed: 'Completed',
  escalated: 'Escalated',
}

const STATE_ICON: Record<string, typeof CheckCircleIcon> = {
  investigating: MagnifyingGlassIcon,
  planned: ClipboardDocumentListIcon,
  resolving: WrenchScrewdriverIcon,
  in_review: EyeIcon,
  completed: CheckCircleIcon,
  escalated: MegaphoneIcon,
}

// Six genuinely distinct, saturated colors -- not just red/green, and not
// two states sharing one color (resolving and in_review used to both land
// on "orange"). Each is only ever used for a small icon, never as a full
// card/strip background tint (that read as "ugly" -- a card's real content
// should stay neutral, with color reserved for the one glanceable accent).
const STATE_COLOR: Record<string, { fg: string; bg: string }> = {
  investigating: { fg: 'var(--color-icon-cyan)', bg: 'var(--color-background-cyan)' },
  planned: { fg: 'var(--color-icon-purple)', bg: 'var(--color-background-purple)' },
  resolving: { fg: 'var(--color-icon-yellow)', bg: 'var(--color-background-yellow)' },
  in_review: { fg: 'var(--color-icon-pink)', bg: 'var(--color-background-pink)' },
  completed: { fg: 'var(--color-icon-green)', bg: 'var(--color-background-green)' },
  escalated: { fg: 'var(--color-icon-red)', bg: 'var(--color-background-red)' },
}

// The elegant, non-button filter row: a single-select set of labels, each
// carrying its own count, "All" first and active by default. Not styled as
// buttons on purpose -- clicking one narrows the list to just that state;
// there's no multi-select, since seeing several states at once alongside
// "all of them" is the exact redundant utility this was asked to drop.
function FilterTabs({
  counts,
  active,
  onSelect,
}: {
  counts: Record<string, number>
  active: string | null
  onSelect: (state: string | null) => void
}) {
  const total = Object.values(counts).reduce((sum, n) => sum + n, 0)
  return (
    <HStack gap={4} wrap="wrap" align="center">
      <FilterTab label="All" count={total} isActive={active === null} onClick={() => onSelect(null)} />
      {STATE_ORDER.map((state) => (
        <FilterTab
          key={state}
          label={STATE_LABEL[state]}
          count={counts[state] ?? 0}
          isActive={active === state}
          onClick={() => onSelect(state)}
        />
      ))}
    </HStack>
  )
}

function FilterTab({
  label,
  count,
  isActive,
  onClick,
}: {
  label: string
  count: number
  isActive: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        border: 'none',
        background: 'none',
        padding: '2px 0',
        cursor: 'pointer',
        display: 'flex',
        alignItems: 'baseline',
        gap: 5,
        borderBottom: isActive ? '2px solid var(--color-text-primary)' : '2px solid transparent',
      }}
    >
      <Text
        type="body"
        weight={isActive ? 'bold' : 'normal'}
        style={{ color: isActive ? 'var(--color-text-primary)' : 'var(--color-text-secondary)' }}
      >
        {label}
      </Text>
      <Text type="body" size="sm" color="secondary">
        {count}
      </Text>
    </button>
  )
}

function StateIcon({ state, size = 32 }: { state: string; size?: number }) {
  const Icon = STATE_ICON[state] ?? ClipboardDocumentListIcon
  const { fg, bg } = STATE_COLOR[state] ?? STATE_COLOR.investigating
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: bg,
      }}
    >
      <Icon style={{ width: Math.round(size * 0.5), height: Math.round(size * 0.5), color: fg }} />
    </div>
  )
}

// Deliberately smaller and more muted than the card's other actions -- these
// are secondary, low-frequency affordances (peek at comments, grab a link),
// not primary calls to action, so they shouldn't compete visually with the
// title or the delete control.
const MINOR_ACTION_STYLE = { height: 20, minHeight: 20, padding: '0 4px', color: 'var(--color-text-secondary)' }
const MINOR_ICON_STYLE = { width: 12, height: 12, color: 'var(--color-icon-secondary)' }

function TicketCard({
  ticket,
  onOpen,
  onDelete,
}: {
  ticket: TicketOut
  onOpen: () => void
  onDelete: () => void
}) {
  const [justCopied, setJustCopied] = useState(false)
  const caption = ticket.latest_event_name
    ? describeLatestAction(ticket.latest_event_name, ticket.latest_event_status ?? 'success')
    : 'No activity yet'

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

  return (
    <Card elevation="low" padding={4} onClick={onOpen} style={{ cursor: 'pointer', borderRadius: 'var(--radius-inner)' }}>
      <VStack gap={3}>
        <HStack align="start" gap={3}>
          <VStack gap={1} style={{ minWidth: 0, flex: 1 }}>
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
            <HStack gap={2} align="center">
              <StateIcon state={ticket.state} size={18} />
              <Text type="body" size="sm" color="secondary">
                {formatRelative(ticket.created_at)}
                {ticket.resource_id ? ` · ${ticket.resource_id}` : ''}
              </Text>
            </HStack>
          </VStack>
          <Button
            label="Delete ticket"
            isIconOnly
            size="sm"
            variant="ghost"
            icon={<XMarkIcon style={{ width: 14, height: 14 }} />}
            onClick={(event) => {
              event.stopPropagation()
              onDelete()
            }}
          />
        </HStack>

        <VStack gap={2}>
          <Text type="body" size="sm" weight="semibold">
            {caption}
            {ticket.latest_event_ts && (
              <Text as="span" type="body" size="sm" color="secondary">
                {' '}
                · {formatRelative(ticket.latest_event_ts)}
              </Text>
            )}
          </Text>
          <div style={{ borderBottom: '1px solid var(--color-border)', width: '100%' }} />
        </VStack>

        <HStack align="center" style={{ justifyContent: 'flex-end' }}>
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
          </HStack>
        </HStack>
      </VStack>
    </Card>
  )
}

function DeleteTicketDialog({
  ticket,
  onCancel,
  onConfirm,
}: {
  ticket: TicketOut | null
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <Dialog isOpen={ticket !== null} onOpenChange={(open) => !open && onCancel()} width={420} purpose="form">
      {ticket && (
        <Layout
          header={<DialogHeader title="Delete ticket?" onOpenChange={onCancel} />}
          content={
            <LayoutContent>
              <VStack gap={4}>
                <Text type="body">
                  Delete "{ticket.title}"? Its logged events will be kept, just unassigned from this ticket.
                </Text>
                <HStack gap={2} style={{ justifyContent: 'flex-end' }}>
                  <Button label="Cancel" variant="secondary" onClick={onCancel} />
                  <Button label="Delete" variant="destructive" onClick={onConfirm} />
                </HStack>
              </VStack>
            </LayoutContent>
          }
        />
      )}
    </Dialog>
  )
}

function sortByRecency(tickets: TicketOut[]): TicketOut[] {
  return tickets.slice().sort((a, b) => b.id - a.id)
}

// One state's section: a small header (icon + label + count) the cards
// themselves no longer repeat -- showing the same icon on every card in an
// already-grouped section said nothing a grid full of "Escalated" cards
// under an "Escalated" header didn't already say -- followed by a grid of
// that state's cards, several per row.
function StateSection({
  state,
  tickets,
  onOpen,
  onDelete,
}: {
  state: string
  tickets: TicketOut[]
  onOpen: (ticket: TicketOut) => void
  onDelete: (ticket: TicketOut) => void
}) {
  if (tickets.length === 0) return null
  return (
    <VStack gap={3}>
      <HStack align="center" gap={2}>
        <StateIcon state={state} />
        <Heading level={4}>{STATE_LABEL[state] ?? state}</Heading>
        <Text type="body" color="secondary">
          {tickets.length}
        </Text>
      </HStack>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 16 }}>
        {sortByRecency(tickets).map((ticket) => (
          <TicketCard
            key={ticket.id}
            ticket={ticket}
            onOpen={() => onOpen(ticket)}
            onDelete={() => onDelete(ticket)}
          />
        ))}
      </div>
    </VStack>
  )
}

// The Dashboard's ticket area: subsections in priority order (whatever most
// needs an operator's attention first), each a grid of that state's cards --
// not one flat list, and not side-by-side kanban columns either. The filter
// tab row narrows which subsection(s) show; "All" (default) shows every
// non-empty one, stacked top to bottom.
export function TicketsGrid({ tickets, onChanged }: { tickets: TicketOut[]; onChanged: () => void }) {
  const navigate = useNavigate()
  const [activeState, setActiveState] = useState<string | null>(null)
  const [pendingDelete, setPendingDelete] = useState<TicketOut | null>(null)

  const counts = useMemo(() => {
    const result: Record<string, number> = {}
    for (const ticket of tickets) result[ticket.state] = (result[ticket.state] ?? 0) + 1
    return result
  }, [tickets])

  const byState = useMemo(() => {
    const result: Record<string, TicketOut[]> = {}
    for (const ticket of tickets) (result[ticket.state] ??= []).push(ticket)
    return result
  }, [tickets])

  const visibleStates = activeState === null ? STATE_ORDER : [activeState]

  async function confirmDelete() {
    if (!pendingDelete) return
    await api.deleteTicket(pendingDelete.id)
    setPendingDelete(null)
    onChanged()
  }

  const hasAnyVisible = visibleStates.some((state) => (byState[state]?.length ?? 0) > 0)

  return (
    <VStack gap={5}>
      <FilterTabs counts={counts} active={activeState} onSelect={setActiveState} />
      {!hasAnyVisible ? (
        <Text type="body" color="secondary">
          {tickets.length === 0 ? 'No tickets yet.' : 'No tickets in this state.'}
        </Text>
      ) : (
        visibleStates.map((state) => (
          <StateSection
            key={state}
            state={state}
            tickets={byState[state] ?? []}
            onOpen={(ticket) => navigate(`/logs?ticket=${ticket.id}`)}
            onDelete={setPendingDelete}
          />
        ))
      )}
      <DeleteTicketDialog ticket={pendingDelete} onCancel={() => setPendingDelete(null)} onConfirm={confirmDelete} />
    </VStack>
  )
}
