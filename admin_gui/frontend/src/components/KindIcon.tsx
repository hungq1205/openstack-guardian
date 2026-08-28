import { BoltIcon, ChatBubbleLeftRightIcon, DocumentTextIcon } from '@heroicons/react/24/outline'

// A categorical "what kind of call is this" indicator -- tool/resource/prompt
// -- using Astryx's built-in categorical color tokens. This is deliberately
// never a status signal: success/error stays communicated only by the red
// bar elsewhere, so this dot/icon means the same thing whether a call
// succeeded or failed.
const KIND_CONFIG = {
  tool: { icon: BoltIcon, bg: 'var(--color-background-blue)', fg: 'var(--color-icon-blue)' },
  resource: { icon: DocumentTextIcon, bg: 'var(--color-background-purple)', fg: 'var(--color-icon-purple)' },
  prompt: { icon: ChatBubbleLeftRightIcon, bg: 'var(--color-background-teal)', fg: 'var(--color-icon-teal)' },
} as const

function configFor(kind: string) {
  return KIND_CONFIG[kind as keyof typeof KIND_CONFIG] ?? KIND_CONFIG.tool
}

export function KindIcon({ kind, size = 32 }: { kind: string; size?: number }) {
  const config = configFor(kind)
  const Icon = config.icon
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
        background: config.bg,
      }}
    >
      <Icon style={{ width: size * 0.5, height: size * 0.5, color: config.fg }} />
    </div>
  )
}

export function KindDot({ kind, size = 8 }: { kind: string; size?: number }) {
  const config = configFor(kind)
  return (
    <div style={{ width: size, height: size, borderRadius: '50%', background: config.fg, flexShrink: 0 }} />
  )
}
