// Every timestamp in this app is rendered in Bangkok local time (UTC+7) --
// the operator's timezone -- regardless of the browser's own timezone, with
// seconds rounded off everywhere except the full detail view.
export const BANGKOK_TZ = 'Asia/Bangkok'

function bangkokDateKey(ts: string): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: BANGKOK_TZ }).format(new Date(ts))
}

// Groups a list of already-sorted events into day headers: "Today",
// "Yesterday", or a full date, matching how the operator thinks about a
// timeline rather than a raw calendar date.
export function formatDayLabel(ts: string): string {
  const key = bangkokDateKey(ts)
  const now = new Date()
  const todayKey = bangkokDateKey(now.toISOString())
  const yesterdayKey = bangkokDateKey(new Date(now.getTime() - 86400000).toISOString())
  if (key === todayKey) return 'Today'
  if (key === yesterdayKey) return 'Yesterday'
  return new Intl.DateTimeFormat('en-US', {
    timeZone: BANGKOK_TZ,
    weekday: undefined,
    month: 'long',
    day: 'numeric',
    year: 'numeric',
  }).format(new Date(ts))
}

export function formatTimeOnly(ts: string): string {
  return new Intl.DateTimeFormat('en-US', {
    timeZone: BANGKOK_TZ,
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
    timeZone: BANGKOK_TZ,
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(rounded)
}

export function formatFullTime(ts: string): string {
  const formatted = new Intl.DateTimeFormat('en-US', {
    timeZone: BANGKOK_TZ,
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(ts))
  return `${formatted} (UTC+7, Asia/Bangkok)`
}

export function formatRelative(ts: string): string {
  const diffMs = Date.now() - new Date(ts).getTime()
  const minutes = Math.round(diffMs / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  return `${days}d ago`
}

export function formatDuration(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`
}

// Converts a <input type="datetime-local"> value -- entered as Bangkok
// wall-clock time, since that's how every timestamp in this app is displayed
// -- into a UTC ISO string for the events API's since/until filters. Bangkok
// has no DST, so the offset is always a flat 7 hours.
export function bangkokLocalToUtcIso(datetimeLocal: string): string | undefined {
  if (!datetimeLocal) return undefined
  const [datePart, timePart] = datetimeLocal.split('T')
  const [year, month, day] = datePart.split('-').map(Number)
  const [hour, minute] = timePart.split(':').map(Number)
  const utcMs = Date.UTC(year, month - 1, day, hour, minute) - 7 * 60 * 60 * 1000
  return new Date(utcMs).toISOString()
}
