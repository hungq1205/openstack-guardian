import { Fragment, type ReactNode } from 'react'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Heading, Text } from '@astryxdesign/core/Text'
import { BORDER, CodeInline, INK, TEXT } from './CodePanel'

// Matches, inside a stretch of prose, the shapes worth calling out as a
// quick code reference: a key=value or key: value token (id=4a76a7df-...,
// power_state: running), a scheme:// resource URI (cmp://server-v1/{id}), a
// UUID (04ec7308-8cfc-...), a bare snake_case identifier (a tool/pattern
// name -- admin_api_servers_recreate, connection_aborted_transient), or an
// HTTP status phrase (202 accepted, case-insensitive since models don't
// reliably match the spec's capitalization). The key=value/key: value/URI/
// UUID alternatives come first specifically so `power_state: running`
// matches whole -- JS regex alternation takes the first alternative that
// succeeds at a given position, not the longest, so if bare snake_case ran
// first it would grab just `power_state` and leave `: running` behind (it
// has an underscore and needs no colon to match). Two lowercase parts
// joined by an underscore is a low enough bar to occasionally flag an
// ordinary compound word, and key: value is common enough in plain
// sentences ("Cause: transient failure...") to occasionally over-annotate
// too -- but a false positive here just reads as slightly-over-eager
// styling, never as wrong information -- a fine trade for not missing the
// tool/pattern names, resource ids, and field values that are the actual
// point of annotating this at all.
const INLINE_REF_PATTERN = new RegExp(
  [
    String.raw`\b[a-zA-Z_][a-zA-Z0-9_]*=[^\s,;]+`,
    String.raw`\b[a-z_][a-z0-9_]*:\s[a-zA-Z0-9_.\/{}-]+\b`,
    String.raw`\b[a-z][a-z0-9+.-]*:\/\/[^\s,;)]+`,
    String.raw`\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b`,
    String.raw`\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b`,
    String.raw`\b\d{3}\s(?:OK|Created|Accepted|No Content|Bad Request|Unauthorized|Forbidden|Not Found|Conflict|Internal Server Error|Service Unavailable|Gateway Timeout)\b`,
  ].join('|'),
  'gi',
)

// A pasted log line the model didn't wrap in quotes -- still worth its own
// block, matched directly by the level tag that starts it (ERROR/WARN/INFO/
// DEBUG) through to the next sentence boundary (a period immediately
// followed by whitespace or end of string). Periods embedded in the log
// line itself -- IPs, versions, "v2.1" -- are never followed by whitespace,
// so they don't false-trigger the boundary; only a real prose sentence
// break does.
const UNQUOTED_LOG_PATTERN = /\b(?:ERROR|WARN(?:ING)?|INFO|DEBUG)\b[\s\S]*?(?:\.(?=\s|$)|$)/g

// A long, timestamped, or level-tagged quote reads as a pasted log line, not
// a short paraphrase -- only that shape earns its own block below the
// sentence introducing it; an ordinary short quoted phrase (e.g. quoting a
// knowledge-base field back) stays inline, since pulling every quote out
// would fragment normal prose for no benefit.
function looksLikeLogLine(quoted: string): boolean {
  return (
    quoted.length > 80 ||
    /\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}/.test(quoted) ||
    /\b(?:ERROR|WARN(?:ING)?|INFO|DEBUG)\b/.test(quoted)
  )
}

function InlineRef({ children }: { children: ReactNode }) {
  return (
    <span
      style={{
        fontFamily: 'var(--font-family-code)',
        fontSize: '0.9em',
        color: TEXT,
        background: INK,
        border: `1px solid ${BORDER}`,
        borderRadius: 3,
        padding: '0 4px',
      }}
    >
      {children}
    </span>
  )
}

function renderInlineRefs(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  let lastIndex = 0
  let index = 0
  // matchAll (unlike a manual exec loop) clones the regex internally rather
  // than mutating INLINE_REF_PATTERN's own `lastIndex` -- safe to call
  // concurrently/repeatedly against the same shared pattern.
  for (const match of text.matchAll(INLINE_REF_PATTERN)) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index))
    nodes.push(<InlineRef key={`${keyPrefix}-${index++}`}>{match[0]}</InlineRef>)
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex))
  return nodes
}

const QUOTE_PATTERN = /"([^"]{20,})"/g

type LogSpan = { start: number; end: number; content: string }

// Every span of `text` worth breaking out into its own log block, quoted or
// not, sorted left to right with overlaps resolved in favor of whichever
// span was found first in that order -- a quoted log line's opening quote
// sits one character before where the unquoted pattern would also start
// matching the same "ERROR ..." text, so it always sorts first and wins.
function findLogSpans(text: string): LogSpan[] {
  const quoted: LogSpan[] = []
  for (const match of text.matchAll(QUOTE_PATTERN)) {
    if (!looksLikeLogLine(match[1])) continue
    quoted.push({ start: match.index, end: match.index + match[0].length, content: match[1] })
  }
  const unquoted: LogSpan[] = []
  for (const match of text.matchAll(UNQUOTED_LOG_PATTERN)) {
    unquoted.push({ start: match.index, end: match.index + match[0].length, content: match[0] })
  }
  const merged: LogSpan[] = []
  for (const span of [...quoted, ...unquoted].sort((a, b) => a.start - b.start)) {
    const last = merged[merged.length - 1]
    if (last && span.start < last.end) continue
    merged.push(span)
  }
  return merged
}

// One field's prose: a pasted log line breaks out into its own block (so a
// dense multi-clause line doesn't run together with the sentence
// introducing it) whether or not the model actually quoted it, and short
// technical references get a light monospace annotation -- normal body text
// otherwise, not a code/terminal treatment.
function ProseText({ text }: { text: string }) {
  const blocks: ReactNode[] = []
  let lastIndex = 0
  let blockIndex = 0
  for (const span of findLogSpans(text)) {
    const before = text.slice(lastIndex, span.start).trim()
    if (before) {
      blocks.push(
        <Text key={`t-${blockIndex}`} type="body" style={{ whiteSpace: 'pre-wrap' }}>
          {renderInlineRefs(before, `t-${blockIndex}`)}
        </Text>,
      )
    }
    blocks.push(
      <div
        key={`q-${blockIndex}`}
        style={{
          background: INK,
          border: `1px solid ${BORDER}`,
          borderRadius: 'var(--radius-container)',
          padding: '8px 10px',
          color: TEXT,
          fontFamily: 'var(--font-family-code)',
          fontSize: 12,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {span.content}
      </div>,
    )
    lastIndex = span.end
    blockIndex++
  }
  const rest = text.slice(lastIndex).trim()
  if (rest) {
    blocks.push(
      <Text key={`t-${blockIndex}`} type="body" style={{ whiteSpace: 'pre-wrap' }}>
        {renderInlineRefs(rest, `t-${blockIndex}`)}
      </Text>,
    )
  }
  if (blocks.length === 0) return null
  return <VStack gap={2}>{blocks}</VStack>
}

// A plan or report read as a document: a title naming each field, then its
// prose directly underneath -- not tool-call arguments (key: "value" lines
// meant for debugging a real API request). These two tools have no API
// request behind them at all, so the dark inspector treatment every other
// tool's Parameters/Result get would be actively misleading here, not just
// less readable. `meta` (the resource id, the matched pattern id) sits above
// everything as small inline chips, since those are identifiers to
// reference, not prose to read.
export function FieldCards({
  fields,
  meta,
}: {
  fields: { label: string; value: string }[]
  meta?: { label: string; value: string }[]
}) {
  return (
    <VStack gap={4}>
      {meta && meta.length > 0 && (
        <HStack gap={4} wrap="wrap">
          {meta.map((item) => (
            <HStack gap={1} align="center" key={item.label}>
              <Text type="body" size="sm" color="secondary">
                {item.label}:
              </Text>
              <CodeInline>{item.value}</CodeInline>
            </HStack>
          ))}
        </HStack>
      )}
      {fields.map((field) => (
        <Fragment key={field.label}>
          <hr style={{ width: '100%', border: 'none', borderTop: '1px solid var(--color-border)' }} />
          <VStack gap={2}>
            <Heading level={4}>{field.label}</Heading>
            <ProseText text={field.value} />
          </VStack>
        </Fragment>
      ))}
    </VStack>
  )
}
