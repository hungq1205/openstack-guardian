// The admin GUI's visual direction: a light operations workspace (grouped
// side nav, card-based dashboard, day-grouped activity timeline) with a
// fixed-dark top bar for orientation and dark "inspector" panels wherever
// raw technical content -- request/response-shaped parameters, results,
// regexes -- needs to read like a terminal rather than a form.
//
// Every token that is highly visible (surfaces, text, the accent) is
// hand-picked as an explicit [light, dark] tuple below rather than left to
// `color.accent`'s automatic HCT derivation: a prior pass trusted the
// algorithm for the whole palette and it quietly muddied the light-mode
// accent relative to the specimen that was actually approved. Explicit
// `tokens` always win over generated ones (see defineTheme's precedence),
// so what's written here is exactly what ships.
import { defineTheme } from '@astryxdesign/core/theme'
import { neutralTheme } from '@astryxdesign/theme-neutral/built'

export const cmpConsoleTheme = defineTheme({
  name: 'cmp-console',
  extends: neutralTheme,
  typography: {
    scale: { base: 14, ratio: 1.25 },
    body: {
      family: '-apple-system',
      fallbacks: 'BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
    },
    code: {
      family: 'ui-monospace',
      fallbacks: '"SF Mono", "Cascadia Code", Consolas, monospace',
    },
  },
  motion: { fast: 175, medium: 410, slow: 975, ratio: 0.75 },
  tokens: {
    // Surfaces: near-white body / pure-white cards in light mode, near-black
    // body / lifted-graphite cards in dark mode -- cool, not warm.
    '--color-background-body': ['#F5F6F8', '#0B0C10'],
    '--color-background-surface': ['#FFFFFF', '#16171D'],
    '--color-background-card': ['#FFFFFF', '#1B1D24'],
    '--color-background-popover': ['#FFFFFF', '#1E2027'],
    '--color-background-muted': ['#F0F1F4', '#22242C'],

    '--color-text-primary': ['#14161A', '#EDEEF2'],
    '--color-text-secondary': ['#5B6472', '#9AA0AC'],

    '--color-icon-primary': ['#14161A', '#EDEEF2'],
    '--color-icon-secondary': ['#5B6472', '#9AA0AC'],

    '--color-border': ['#E4E7EC', '#262931'],
    '--color-border-emphasized': ['#C7CCD4', '#383C46'],

    // One accent -- a confident indigo, not derived. Used with intention:
    // primary actions, selected nav state, links, live/enabled indicators.
    '--color-accent': ['#4F46E5', '#6366F1'],
    '--color-accent-muted': ['#4F46E51F', '#6366F142'],
    '--color-on-accent': ['#FFFFFF', '#FFFFFF'],
    '--color-text-accent': ['#4F46E5', '#A5B4FC'],
    '--color-icon-accent': ['#4F46E5', '#A5B4FC'],
  },
  components: {
    // The selected sidebar item pops with the accent (tinted background,
    // accent text/icon via `inherit`), rather than Astryx's default
    // deemphasized neutral highlight -- matching the reference's contrast.
    'side-nav-item': {
      selected: {
        backgroundColor: 'var(--color-accent-muted)',
        color: 'var(--color-text-accent)',
      },
    },
  },
})
