// Display-only preferences, local to this browser -- this app is a
// single-operator, no-auth local tool (see CLAUDE.md), so there's no
// server-side "account" to hang a preference off of; localStorage is enough,
// and keeps this independent of the SQLite-backed config_store the MCP
// servers themselves read.
const TIMEZONE_STORAGE_KEY = 'guardian:displayTimezone'

export const DEFAULT_TIMEZONE = 'Asia/Bangkok'

export function getDisplayTimezone(): string {
  try {
    return localStorage.getItem(TIMEZONE_STORAGE_KEY) ?? DEFAULT_TIMEZONE
  } catch {
    return DEFAULT_TIMEZONE
  }
}

export function setDisplayTimezone(timeZone: string): void {
  try {
    localStorage.setItem(TIMEZONE_STORAGE_KEY, timeZone)
  } catch {
    // Storage unavailable (private mode, etc.) -- the setting just won't
    // survive a reload, which is a reasonable degradation for a preference.
  }
}

// A curated fallback list, offset-labeled -- Intl.supportedValuesOf exists in
// every evergreen browser this local admin tool targets, so it's tried
// first; the curated list only matters on an engine old enough to lack it.
const CURATED_TIMEZONES = [
  'UTC',
  'Asia/Bangkok',
  'Asia/Ho_Chi_Minh',
  'Asia/Jakarta',
  'Asia/Singapore',
  'Asia/Hong_Kong',
  'Asia/Shanghai',
  'Asia/Tokyo',
  'Asia/Seoul',
  'Asia/Kolkata',
  'Asia/Dubai',
  'Europe/London',
  'Europe/Paris',
  'Europe/Berlin',
  'Europe/Moscow',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'America/Sao_Paulo',
  'Australia/Sydney',
  'Pacific/Auckland',
]

export function listTimezones(): string[] {
  const intlWithSupport = Intl as typeof Intl & { supportedValuesOf?: (key: string) => string[] }
  if (typeof intlWithSupport.supportedValuesOf === 'function') {
    try {
      return intlWithSupport.supportedValuesOf('timeZone')
    } catch {
      // fall through to the curated list below
    }
  }
  return CURATED_TIMEZONES
}

// "Asia/Bangkok (UTC+07:00)" -- computed for the given instant since a
// timezone's offset isn't a fixed property of the zone itself (DST).
export function formatTimezoneOffset(timeZone: string, at: number = Date.now()): string {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone, timeZoneName: 'shortOffset' }).formatToParts(
    new Date(at),
  )
  const offset = parts.find((p) => p.type === 'timeZoneName')?.value ?? ''
  return offset.replace('GMT', 'UTC')
}
