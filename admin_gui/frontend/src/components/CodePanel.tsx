import { useState, type CSSProperties, type ReactNode } from 'react'
import { CheckIcon, ClipboardIcon } from '@heroicons/react/24/outline'

// A fixed-dark "inspector" surface for raw technical content -- JSON
// arguments/results, regexes, operation ids -- so it reads like a terminal
// regardless of the ambient light/dark theme. Colors are literal, not
// theme tokens: this panel is deliberately the same in both modes. Exported
// so other components (FieldCards' inline code references and pasted-log
// blocks) can match this exact palette without duplicating it.
export const INK = '#0F1014'
export const BORDER = '#262931'
export const TEXT = '#D6D9E0'
const KEY = '#E4E6EB'
const STRING = '#6EE7A8'
const NUMBER = '#FBBF77'
const BOOLEAN = '#C4B5FD'
const NULLISH = '#6B7280'

const panelStyle: CSSProperties = {
  position: 'relative',
  background: INK,
  border: `1px solid ${BORDER}`,
  borderRadius: 'var(--radius-container)',
  padding: '12px 14px',
  fontFamily: 'var(--font-family-code)',
  fontSize: 12.5,
  lineHeight: 1.7,
  display: 'flex',
  flexDirection: 'column',
  gap: 4,
  overflowX: 'auto',
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  function fallbackCopy() {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
  }

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      fallbackCopy()
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      aria-label={copied ? 'Copied' : 'Copy to clipboard'}
      style={{
        position: 'absolute',
        top: 8,
        right: 8,
        display: 'flex',
        alignItems: 'center',
        gap: 4,
        border: `1px solid ${BORDER}`,
        borderRadius: 4,
        background: 'rgba(255,255,255,0.04)',
        color: copied ? STRING : TEXT,
        fontFamily: 'var(--font-family-body)',
        fontSize: 11,
        padding: '3px 6px',
        cursor: 'pointer',
      }}
    >
      {copied ? <CheckIcon style={{ width: 12, height: 12 }} /> : <ClipboardIcon style={{ width: 12, height: 12 }} />}
      {copied ? 'Copied' : 'Copy'}
    </button>
  )
}

function renderValue(value: unknown): ReactNode {
  if (value === null || value === undefined) {
    return <span style={{ color: NULLISH, fontStyle: 'italic' }}>null</span>
  }
  if (typeof value === 'string') {
    return <span style={{ color: STRING }}>"{value}"</span>
  }
  if (typeof value === 'number') {
    return <span style={{ color: NUMBER }}>{value}</span>
  }
  if (typeof value === 'boolean') {
    return <span style={{ color: BOOLEAN }}>{String(value)}</span>
  }
  return <span style={{ color: TEXT }}>{JSON.stringify(value)}</span>
}

// Renders a JSON blob (tool/call arguments, results) as key: value lines
// inside the dark inspector panel, instead of a raw pretty-printed block.
// A copy-to-clipboard affordance sits in the corner, same as a REQUEST/
// RESPONSE body panel would carry one -- copies the underlying JSON text,
// not the reformatted key/value display.
export function CodePanel({ jsonText, emptyLabel }: { jsonText: string; emptyLabel: string }) {
  let parsed: unknown
  try {
    parsed = JSON.parse(jsonText)
  } catch {
    return (
      <div style={panelStyle}>
        {jsonText && <CopyButton text={jsonText} />}
        <span style={{ color: TEXT, paddingInlineEnd: jsonText ? 60 : 0 }}>{jsonText || emptyLabel}</span>
      </div>
    )
  }
  if (parsed === null || typeof parsed !== 'object') {
    return (
      <div style={panelStyle}>
        <CopyButton text={jsonText} />
        {renderValue(parsed)}
      </div>
    )
  }
  const entries = Array.isArray(parsed)
    ? parsed.map((value, index) => [String(index), value] as const)
    : Object.entries(parsed as Record<string, unknown>)
  if (entries.length === 0) {
    return (
      <div style={panelStyle}>
        <span style={{ color: NULLISH }}>{emptyLabel}</span>
      </div>
    )
  }
  return (
    <div style={panelStyle}>
      <CopyButton text={jsonText} />
      {entries.map(([key, value]) => (
        <div key={key} style={{ display: 'flex', gap: 8, paddingInlineEnd: 60 }}>
          <span style={{ color: KEY }}>{key}:</span>
          {renderValue(value)}
        </div>
      ))}
    </div>
  )
}

// A single technical value (regex, operation id, uri template) rendered as
// a small dark monospace chip -- the same inspector language as CodePanel,
// used inline in tables and headings.
export function CodeInline({ children }: { children: ReactNode }) {
  return (
    <span
      style={{
        display: 'inline-block',
        fontFamily: 'var(--font-family-code)',
        fontSize: 12,
        color: TEXT,
        background: INK,
        border: `1px solid ${BORDER}`,
        borderRadius: 4,
        padding: '2px 6px',
        maxWidth: '100%',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  )
}
