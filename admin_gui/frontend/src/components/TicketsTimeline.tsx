import { useEffect, useMemo, useRef, useState } from 'react'
import { MagnifyingGlassMinusIcon, MagnifyingGlassPlusIcon } from '@heroicons/react/24/outline'
import { Badge } from '@astryxdesign/core/Badge'
import { Button } from '@astryxdesign/core/Button'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Text } from '@astryxdesign/core/Text'
import { DateTimeField } from './DateTimeField'
import { api, type TicketTransitionOut } from '../lib/api'
import { formatAxisTick, formatFullTime, localInputValueToUtcIso, msToLocalInputValue, timezoneOffsetMs } from '../lib/time'
import { STATE_BADGE_VARIANT, STATE_COLOR, STATE_LABEL } from '../lib/ticketState'

const W = 900
const PANEL_H = 170
const AXIS_H = 20
const MAX_LANES = 4
const LANE_H = (PANEL_H - AXIS_H) / MAX_LANES
// Collision half-width used by packLanes -- kept independent of the marker's
// own rendered size below.
const DOT_R = 6
// Keyframe-style marker: a square rotated 45°, the standard video-editor
// glyph for a point in time.
const MARKER_SIZE = 8
const MIN_GAP_PX = 20
const MIN_SPAN_MS = 2 * 60_000
const ZOOM_SPEED = 0.0015
const ZOOM_BUTTON_FACTOR = 0.6
const EDGE_LABEL_PX = 30

interface Range {
  start: number
  end: number
}

interface LayoutPoint {
  transition: TicketTransitionOut
  px: number
}

interface LayoutTicket {
  ticketId: number
  ticketTitle: string
  points: LayoutPoint[]
  lane: number
}

function timeX(ms: number, range: Range): number {
  return ((ms - range.start) / (range.end - range.start)) * W
}

function screenXToTime(clientX: number, rect: DOMRect, range: Range): number {
  const f = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width))
  return range.start + f * (range.end - range.start)
}

function clampRange(start: number, end: number, bounds: Range): Range {
  const span = end - start
  const boundSpan = bounds.end - bounds.start
  if (span >= boundSpan) return { start: bounds.start, end: bounds.end }
  if (start < bounds.start) return { start: bounds.start, end: bounds.start + span }
  if (end > bounds.end) return { start: bounds.end - span, end: bounds.end }
  return { start, end }
}

function normalizedDeltaY(e: WheelEvent): number {
  if (e.deltaMode === 1) return e.deltaY * 16 // DOM_DELTA_LINE
  if (e.deltaMode === 2) return e.deltaY * window.innerHeight // DOM_DELTA_PAGE
  return e.deltaY // DOM_DELTA_PIXEL
}

// Candidate tick intervals, finest to coarsest -- picked so the chosen one
// keeps roughly TARGET_TICKS on screen no matter how far zoomed in/out.
const TICK_CANDIDATES_MS = [
  1_000,
  5_000,
  15_000,
  30_000,
  60_000,
  5 * 60_000,
  10 * 60_000,
  15 * 60_000,
  30 * 60_000,
  3_600_000,
  2 * 3_600_000,
  3 * 3_600_000,
  4 * 3_600_000,
  6 * 3_600_000,
  12 * 3_600_000,
  86_400_000,
  2 * 86_400_000,
  7 * 86_400_000,
  14 * 86_400_000,
  28 * 86_400_000,
]
const TARGET_TICKS = 7

function pickTickInterval(spanMs: number): number {
  for (const candidate of TICK_CANDIDATES_MS) {
    if (spanMs / candidate <= TARGET_TICKS) return candidate
  }
  return TICK_CANDIDATES_MS[TICK_CANDIDATES_MS.length - 1]
}

// Snaps onto the display timezone's local boundaries (midnight, top-of-hour,
// ...) rather than UTC's -- offset by that zone's real UTC offset at this
// instant (DST-aware) instead of a flat constant.
function alignedTick(ms: number, intervalMs: number): number {
  const offset = timezoneOffsetMs(ms)
  return Math.ceil((ms + offset) / intervalMs) * intervalMs - offset
}

function generateTicks(range: Range, intervalMs: number): number[] {
  const ticks: number[] = []
  for (let t = alignedTick(range.start, intervalMs); t <= range.end; t += intervalMs) ticks.push(t)
  return ticks.length > 0 ? ticks : [range.start + (range.end - range.start) / 2]
}

// Short, unlabeled ruler ticks between the labeled major ones (5 per major
// interval) -- the ruler reads as a real measuring scale rather than a bare
// row of labels, the same small-tick/big-tick hierarchy a video editor's
// timeline ruler uses.
function generateMinorTicks(range: Range, majorIntervalMs: number): number[] {
  const minorIntervalMs = majorIntervalMs / 5
  const offset = timezoneOffsetMs(range.start)
  const ticks: number[] = []
  for (let t = alignedTick(range.start, minorIntervalMs); t <= range.end; t += minorIntervalMs) {
    if ((t + offset) % majorIntervalMs !== 0) ticks.push(t)
  }
  return ticks
}

// Load-balanced lane assignment: each ticket goes to whichever of the
// MAX_LANES rows currently holds the fewest tickets, among the rows it
// doesn't collide with -- spreads density evenly instead of greedily
// filling row 1 first. Only when every row collides does it fall back to
// the lightest-loaded one anyway and accept the overlap, so the panel never
// needs more than MAX_LANES rows. Mutates `.lane` on each entry in place.
//
// Called once against every ticket's FULL extent (positioned against
// fullRange, not the current pan/zoom window) so a ticket's row is a stable
// property of the data, never of what's currently in view -- panning or
// zooming must never reshuffle rows other tickets already sit in.
function packLanes(entries: LayoutTicket[]): void {
  const sorted = [...entries].sort((a, b) => a.points[0].px - b.points[0].px)
  const laneEnds: number[] = new Array(MAX_LANES).fill(-Infinity)
  const laneCounts: number[] = new Array(MAX_LANES).fill(0)
  sorted.forEach((entry) => {
    const startPx = entry.points[0].px - DOT_R
    const endPx = entry.points[entry.points.length - 1].px + DOT_R
    const freeLanes: number[] = []
    for (let i = 0; i < MAX_LANES; i++) {
      if (laneEnds[i] === -Infinity || laneEnds[i] + MIN_GAP_PX < startPx) freeLanes.push(i)
    }
    const lane =
      freeLanes.length > 0
        ? freeLanes.reduce((best, i) => (laneCounts[i] < laneCounts[best] ? i : best), freeLanes[0])
        : laneCounts.reduce((best, count, i) => (count < laneCounts[best] ? i : best), 0)
    laneEnds[lane] = Math.max(laneEnds[lane], endPx)
    laneCounts[lane]++
    entry.lane = lane
  })
}

// A ticket-state-change timeline built like a video editor's clip track: one
// dot per transition, colored by the state it moved into, arcs connecting a
// ticket's own dots through a crowded moment instead of giving every ticket
// a dedicated row. Rows are packed (load-balanced, capped at MAX_LANES) and
// centered in a fixed-size panel. The view starts fully zoomed out; the
// mouse wheel zooms in/out anchored under the cursor, dragging directly on
// the plot pans, and a compact From/To pair jumps the visible range by typed
// value for a coarse cut before fine-tuning by scroll/drag. Clicking a dot
// calls `onSelectTicket`, which the Tickets page uses to filter its list --
// the same clear affordance that filter uses (`onSelectTicket(null)`) also
// clears it here.
export function TicketsTimeline({
  highlightedTicketId,
  onSelectTicket,
}: {
  highlightedTicketId: number | null
  onSelectTicket: (ticketId: number | null) => void
}) {
  const [transitions, setTransitions] = useState<TicketTransitionOut[]>([])
  const [fullRange, setFullRange] = useState<Range | null>(null)
  const [range, setRange] = useState<Range | null>(null)
  const [hovered, setHovered] = useState<{ transition: TicketTransitionOut; ticketTitle: string } | null>(null)
  const dragRef = useRef<{ startClientX: number; rect: DOMRect; panStartRange: Range } | null>(null)
  const plotRef = useRef<SVGSVGElement>(null)
  const fullRangeRef = useRef<Range | null>(null)
  const rangeRef = useRef<Range | null>(null)

  useEffect(() => {
    api
      .listTicketTransitions()
      .then((data) => {
        setTransitions(data)
        if (data.length === 0) return
        const times = data.map((t) => new Date(t.ts).getTime())
        const start = Math.min(...times)
        const end = Math.max(...times)
        const pad = Math.max((end - start) * 0.03, 5 * 60_000)
        const bounds = { start: start - pad, end: end + pad }
        setFullRange(bounds)
        fullRangeRef.current = bounds
        setRange(bounds)
        rangeRef.current = bounds
      })
      .catch(() => undefined)
  }, [])

  const hasMounted = range !== null

  // Wheel-to-zoom needs a native, non-passive listener -- React's onWheel is
  // passive by default, which silently drops preventDefault(). Registered
  // once; reads current range/bounds via refs kept in sync below so it never
  // needs to re-subscribe.
  useEffect(() => {
    const el = plotRef.current
    if (!el) return
    function handleWheel(e: WheelEvent) {
      e.preventDefault()
      const current = rangeRef.current
      const bounds = fullRangeRef.current
      if (!current || !bounds || !el) return
      const rect = el.getBoundingClientRect()
      const anchorMs = screenXToTime(e.clientX, rect, current)
      const d = Math.min(100, Math.max(-100, normalizedDeltaY(e)))
      if (d === 0) return
      const factor = Math.exp(d * ZOOM_SPEED)
      const oldSpan = current.end - current.start
      const newSpan = Math.min(Math.max(oldSpan * factor, MIN_SPAN_MS), bounds.end - bounds.start)
      const f = (anchorMs - current.start) / oldSpan
      const newStart = anchorMs - f * newSpan
      const next = clampRange(newStart, newStart + newSpan, bounds)
      rangeRef.current = next
      setRange(next)
    }
    el.addEventListener('wheel', handleWheel, { passive: false })
    return () => el.removeEventListener('wheel', handleWheel)
    // Re-attach once the SVG actually mounts -- the very first render (before
    // transitions finish loading) hits the early-return placeholder below,
    // so plotRef.current is still null the first time this effect runs.
  }, [hasMounted])

  useEffect(() => {
    function handleMove(event: MouseEvent) {
      const drag = dragRef.current
      const bounds = fullRangeRef.current
      if (!drag || !bounds) return
      const span = drag.panStartRange.end - drag.panStartRange.start
      const deltaMs = ((event.clientX - drag.startClientX) / drag.rect.width) * span
      const newStart = drag.panStartRange.start - deltaMs
      const next = clampRange(newStart, newStart + span, bounds)
      rangeRef.current = next
      setRange(next)
    }
    function handleUp() {
      dragRef.current = null
    }
    window.addEventListener('mousemove', handleMove)
    window.addEventListener('mouseup', handleUp)
    return () => {
      window.removeEventListener('mousemove', handleMove)
      window.removeEventListener('mouseup', handleUp)
    }
  }, [])

  // Stable per-ticket row, computed once from every transition's position
  // across the FULL time range -- see packLanes' comment for why.
  const laneByTicket = useMemo(() => {
    if (!fullRange) return new Map<number, number>()
    const map = new Map<number, LayoutTicket>()
    for (const transition of transitions) {
      const px = timeX(new Date(transition.ts).getTime(), fullRange)
      const entry = map.get(transition.ticket_id) ?? {
        ticketId: transition.ticket_id,
        ticketTitle: transition.ticket_title,
        points: [],
        lane: 0,
      }
      entry.points.push({ transition, px })
      map.set(transition.ticket_id, entry)
    }
    for (const entry of map.values()) entry.points.sort((a, b) => a.px - b.px)
    const entries = [...map.values()]
    packLanes(entries)
    return new Map(entries.map((entry) => [entry.ticketId, entry.lane]))
  }, [transitions, fullRange])

  // The currently-visible slice: which points/arcs to draw, in the current
  // range's pixel space -- but each ticket keeps the row laneByTicket gave
  // it, regardless of which of its points are in view right now.
  const byTicket = useMemo(() => {
    if (!range) return []
    const map = new Map<number, LayoutTicket>()
    for (const transition of transitions) {
      const ms = new Date(transition.ts).getTime()
      if (ms < range.start || ms > range.end) continue
      const px = timeX(ms, range)
      const entry = map.get(transition.ticket_id) ?? {
        ticketId: transition.ticket_id,
        ticketTitle: transition.ticket_title,
        points: [],
        lane: laneByTicket.get(transition.ticket_id) ?? 0,
      }
      entry.points.push({ transition, px })
      map.set(transition.ticket_id, entry)
    }
    for (const entry of map.values()) entry.points.sort((a, b) => a.px - b.px)
    return [...map.values()]
  }, [transitions, range, laneByTicket])

  const { ticks, minorTicks } = useMemo(() => {
    if (!range) return { ticks: [], minorTicks: [] }
    const intervalMs = pickTickInterval(range.end - range.start)
    return {
      ticks: generateTicks(range, intervalMs).map((ms) => ({ ms, label: formatAxisTick(ms, intervalMs) })),
      minorTicks: generateMinorTicks(range, intervalMs),
    }
  }, [range])

  if (transitions.length === 0 || !range || !fullRange) {
    return (
      <Text type="body" size="sm" color="secondary">
        No ticket activity recorded yet.
      </Text>
    )
  }

  // Always MAX_LANES rows, whether or not they're all occupied right now --
  // a fixed row count is itself part of keeping the layout stable as you
  // pan/zoom (no recentering when the currently-visible lane count shifts).
  const yOffset = AXIS_H
  const focusTicketId = hovered ? hovered.transition.ticket_id : highlightedTicketId
  const isZoomedOut = range.start <= fullRange.start && range.end >= fullRange.end

  // Center-anchored zoom for the +/- buttons -- the wheel handler anchors
  // under the cursor instead, since a button click has no meaningful cursor
  // position on the timeline itself.
  function zoomBy(factor: number) {
    if (!range || !fullRange) return
    const mid = (range.start + range.end) / 2
    const oldSpan = range.end - range.start
    const newSpan = Math.min(Math.max(oldSpan * factor, MIN_SPAN_MS), fullRange.end - fullRange.start)
    const newStart = mid - newSpan / 2
    const next = clampRange(newStart, newStart + newSpan, fullRange)
    rangeRef.current = next
    setRange(next)
  }

  function updateRangeFromInput(part: 'start' | 'end', value: string) {
    const iso = localInputValueToUtcIso(value)
    if (!iso || !range || !fullRange) return
    const ms = new Date(iso).getTime()
    const next = part === 'start' ? { start: ms, end: range.end } : { start: range.start, end: ms }
    if (next.end - next.start < MIN_SPAN_MS) return
    const clamped = clampRange(next.start, next.end, fullRange)
    rangeRef.current = clamped
    setRange(clamped)
  }

  return (
    <VStack gap={2}>
      <HStack gap={3} align="end" style={{ justifyContent: 'flex-end' }} wrap="wrap">
        <DateTimeField
          label="From"
          value={msToLocalInputValue(range.start)}
          onChange={(v) => updateRangeFromInput('start', v)}
        />
        <DateTimeField
          label="To"
          value={msToLocalInputValue(range.end)}
          onChange={(v) => updateRangeFromInput('end', v)}
        />
        <HStack gap={1} style={{ paddingBottom: 1 }}>
          <Button
            label="Zoom out"
            isIconOnly
            size="sm"
            variant="ghost"
            icon={<MagnifyingGlassMinusIcon style={{ width: 14, height: 14 }} />}
            onClick={() => zoomBy(1 / ZOOM_BUTTON_FACTOR)}
          />
          <Button
            label="Zoom in"
            isIconOnly
            size="sm"
            variant="ghost"
            icon={<MagnifyingGlassPlusIcon style={{ width: 14, height: 14 }} />}
            onClick={() => zoomBy(ZOOM_BUTTON_FACTOR)}
          />
        </HStack>
        {highlightedTicketId !== null && (
          <button
            type="button"
            onClick={() => onSelectTicket(null)}
            style={{ all: 'unset', cursor: 'pointer', color: 'var(--color-icon-cyan)', fontSize: 12, paddingBottom: 8 }}
          >
            Clear selection
          </button>
        )}
        {!isZoomedOut && (
          <button
            type="button"
            onClick={() => {
              rangeRef.current = fullRange
              setRange(fullRange)
            }}
            style={{ all: 'unset', cursor: 'pointer', color: 'var(--color-icon-cyan)', fontSize: 12, paddingBottom: 8 }}
          >
            Zoom to fit
          </button>
        )}
      </HStack>

      <div style={{ position: 'relative' }}>
      <svg
        ref={plotRef}
        width="100%"
        height={PANEL_H}
        viewBox={`0 0 ${W} ${PANEL_H}`}
        preserveAspectRatio="none"
        style={{ display: 'block', cursor: 'grab', touchAction: 'none' }}
        onMouseDown={(e) => {
          if (!range) return
          const rect = e.currentTarget.getBoundingClientRect()
          dragRef.current = { startClientX: e.clientX, rect, panStartRange: range }
        }}
      >
        {Array.from({ length: MAX_LANES }, (_, lane) =>
          lane % 2 === 1 ? (
            <rect
              key={lane}
              x={0}
              y={yOffset + lane * LANE_H}
              width={W}
              height={LANE_H}
              fill="var(--color-background-muted)"
              opacity={0.4}
            />
          ) : null,
        )}

        {/* Minor ticks: a short notch just above the baseline, no guideline. */}
        {minorTicks.map((ms) => {
          const px = timeX(ms, range)
          return (
            <line
              key={ms}
              x1={px}
              y1={AXIS_H - 3}
              x2={px}
              y2={AXIS_H}
              stroke="var(--color-border)"
              strokeWidth={1}
            />
          )
        })}
        {/* Major ticks: a taller notch plus a faint guideline traced down
            through every lane below -- reads as a ruler graduation that
            actually marks the track content under it, not just a label. */}
        {ticks.map(({ ms }) => {
          const px = timeX(ms, range)
          return (
            <g key={ms}>
              <line x1={px} y1={AXIS_H} x2={px} y2={PANEL_H} stroke="var(--color-border)" strokeWidth={1} opacity={0.5} />
              <line x1={px} y1={AXIS_H - 6} x2={px} y2={AXIS_H} stroke="var(--color-border)" strokeWidth={1} />
            </g>
          )
        })}
        <line x1={0} y1={AXIS_H} x2={W} y2={AXIS_H} stroke="var(--color-border)" strokeWidth={1} />

        {hovered && (
          <line
            x1={timeX(new Date(hovered.transition.ts).getTime(), range)}
            y1={AXIS_H}
            x2={timeX(new Date(hovered.transition.ts).getTime(), range)}
            y2={PANEL_H}
            stroke="var(--color-icon-cyan)"
            strokeWidth={1}
            strokeDasharray="3 3"
            opacity={0.5}
            pointerEvents="none"
          />
        )}

        {byTicket.map(({ ticketId, points, lane }) => {
          const y = yOffset + lane * LANE_H + LANE_H / 2
          const dimmed = focusTicketId !== null && focusTicketId !== ticketId
          return (
            <g key={ticketId} style={{ cursor: 'pointer', opacity: dimmed ? 0.15 : 1, transition: 'opacity .1s ease' }}>
              {points.slice(1).map((point, i) => {
                const prev = points[i]
                const midX = (prev.px + point.px) / 2
                const arcHeight = Math.min(LANE_H / 2 - 3, Math.max(6, (point.px - prev.px) * 0.22))
                return (
                  <path
                    key={point.transition.id}
                    d={`M ${prev.px} ${y} Q ${midX} ${y - arcHeight} ${point.px} ${y}`}
                    fill="none"
                    stroke="var(--color-text-secondary)"
                    strokeWidth={1.25}
                    opacity={0.4}
                  />
                )
              })}
              {points.map(({ transition, px }) => (
                <rect
                  key={transition.id}
                  x={px - MARKER_SIZE / 2}
                  y={y - MARKER_SIZE / 2}
                  width={MARKER_SIZE}
                  height={MARKER_SIZE}
                  rx={1.5}
                  transform={`rotate(45 ${px} ${y})`}
                  fill={(STATE_COLOR[transition.to_state] ?? STATE_COLOR.investigating).fg}
                  stroke="var(--color-background-card)"
                  strokeWidth={2}
                  onMouseDown={(e) => e.stopPropagation()}
                  onMouseEnter={() => setHovered({ transition, ticketTitle: transitions.find((t) => t.ticket_id === ticketId)?.ticket_title ?? '' })}
                  onMouseLeave={() => setHovered(null)}
                  onClick={() => onSelectTicket(highlightedTicketId === ticketId ? null : ticketId)}
                />
              ))}
            </g>
          )
        })}
      </svg>

      {/* Tick labels render as HTML, not SVG <text> -- the plot's viewBox is
          stretched non-uniformly (preserveAspectRatio="none") to fill
          whatever width the container has, which distorts glyph shapes if
          text lives inside that coordinate space. Positioned by the same
          px/W fraction the SVG uses, so labels still line up with their
          tick marks at any container width, just without the stretch. */}
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: AXIS_H, pointerEvents: 'none' }}>
        {ticks.map(({ ms, label }) => {
          const px = timeX(ms, range)
          const anchor = px < EDGE_LABEL_PX ? 'start' : px > W - EDGE_LABEL_PX ? 'end' : 'middle'
          const translateX = anchor === 'middle' ? '-50%' : anchor === 'end' ? '-100%' : '0'
          return (
            <span
              key={ms}
              style={{
                position: 'absolute',
                left: `${(px / W) * 100}%`,
                top: 2,
                transform: `translateX(${translateX})`,
                fontFamily: 'var(--font-family-code)',
                fontSize: 'var(--font-size-xs)',
                color: 'var(--color-text-secondary)',
                whiteSpace: 'nowrap',
              }}
            >
              {label}
            </span>
          )
        })}
      </div>
      </div>

      <div
        style={{
          minHeight: 58,
          border: '1px dashed var(--color-border)',
          borderRadius: 'var(--radius-inner)',
          padding: '10px 12px',
        }}
      >
        {hovered ? (
          <VStack gap={1}>
            <HStack gap={2} align="center">
              <Badge
                variant={STATE_BADGE_VARIANT[hovered.transition.to_state] ?? 'neutral'}
                label={STATE_LABEL[hovered.transition.to_state] ?? hovered.transition.to_state}
              />
              <Text type="body" weight="bold" size="sm">
                #{hovered.transition.ticket_id} {hovered.ticketTitle}
              </Text>
            </HStack>
            <Text type="body" size="sm" color="secondary" style={{ fontFamily: 'var(--font-family-code)' }}>
              {formatFullTime(hovered.transition.ts)}
            </Text>
          </VStack>
        ) : (
          <Text type="body" size="sm" color="secondary">
            Scroll to zoom, drag to pan, hover a dot for details.
          </Text>
        )}
      </div>
    </VStack>
  )
}
