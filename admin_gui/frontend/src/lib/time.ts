import { getDisplayTimezone } from './settings'

// Every timestamp in this app is rendered in the operator's configured
// display timezone (Settings page, defaults to Asia/Bangkok) rather than the
// browser's own timezone, with seconds rounded off everywhere except the
// full detail view. Read fresh on every call (cheap) rather than cached, so
// a change on the Settings page is picked up by the next render anywhere in
// the app without needing a dedicated subscription.
function tz(): string {
  return getDisplayTimezone()
}

function dateKey(ts: string): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: tz() }).format(new Date(ts))
}

// Groups a list of already-sorted events into day headers: "Today",
// "Yesterday", or a full date, matching how the operator thinks about a
// timeline rather than a raw calendar date.
export function formatDayLabel(ts: string): string {
  const key = dateKey(ts)
  const now = new Date()
  const todayKey = dateKey(now.toISOString())
  const yesterdayKey = dateKey(new Date(now.getTime() - 86400000).toISOString())
  if (key === todayKey) return 'Today'
  if (key === yesterdayKey) return 'Yesterday'
  return new Intl.DateTimeFormat('en-US', {
    timeZone: tz(),
    weekday: undefined,
    month: 'long',
    day: 'numeric',
    year: 'numeric',
  }).format(new Date(ts))
}

export function formatTimeOnly(ts: string): string {
  return new Intl.DateTimeFormat('en-US', {
    timeZone: tz(),
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(ts))
}

// Compact overview timestamp: month/day + hour:minute, seconds rounded off.
export function formatOverviewTime(ts: string): string {
  const date = new Date(ts)
  const rounded = new Date(Math.round(date.getTime() / 60000) * 60000)
  return new Intl.DateTimeFormat('en-US', {
    timeZone: tz(),
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(rounded)
}

export function formatFullTime(ts: string): string {
  return new Intl.DateTimeFormat('en-US', {
    timeZone: tz(),
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(ts))
}

export function formatDuration(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`
}

// The UTC offset (ms) of `timeZone` at the given instant -- not a fixed
// property of the zone, since DST shifts it through the year. Computed by
// re-reading the instant's wall-clock digits in that zone and diffing
// against the same digits interpreted as UTC. Exported for the timeline's
// own tick-boundary alignment (snapping ruler ticks onto the display
// timezone's local midnight/top-of-hour, not UTC's).
export function timezoneOffsetMs(ms: number, timeZone: string = tz()): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).formatToParts(new Date(ms))
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? 0)
  const asUtc = Date.UTC(get('year'), get('month') - 1, get('day'), get('hour'), get('minute'), get('second'))
  return asUtc - ms
}

// Converts a <input type="datetime-local"> value -- entered as wall-clock
// time in the display timezone, since that's how every timestamp in this app
// is shown -- into a UTC ISO string for the events API's since/until
// filters and the timeline's range inputs.
export function localInputValueToUtcIso(datetimeLocal: string, timeZone: string = tz()): string | undefined {
  if (!datetimeLocal) return undefined
  const [datePart, timePart] = datetimeLocal.split('T')
  const [year, month, day] = datePart.split('-').map(Number)
  const [hour, minute] = timePart.split(':').map(Number)
  const guessUtc = Date.UTC(year, month - 1, day, hour, minute)
  const offset = timezoneOffsetMs(guessUtc, timeZone)
  return new Date(guessUtc - offset).toISOString()
}

// Inverse of localInputValueToUtcIso -- an epoch ms back into a
// <input type="datetime-local"> value, for the timeline's own From/To
// range-jump control.
export function msToLocalInputValue(ms: number, timeZone: string = tz()): string {
  const d = new Date(ms + timezoneOffsetMs(ms, timeZone))
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`
}

// Axis-tick label for the timeline, banded by the chosen tick interval
// rather than the raw timestamp -- day-scale ticks don't need a time-of-day,
// minute-scale ticks don't need a date.
export function formatAxisTick(ms: number, intervalMs: number): string {
  if (intervalMs >= 86_400_000) {
    return new Intl.DateTimeFormat('en-US', { timeZone: tz(), month: 'short', day: 'numeric' }).format(ms)
  }
  if (intervalMs >= 3_600_000) {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: tz(),
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
    }).format(ms)
  }
  return new Intl.DateTimeFormat('en-US', { timeZone: tz(), hour: 'numeric', minute: '2-digit' }).format(ms)
}
