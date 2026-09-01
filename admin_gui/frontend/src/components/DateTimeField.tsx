import { Text } from '@astryxdesign/core/Text'

// A labeled <input type="datetime-local">, styled to match this app's other
// form controls -- Astryx has no native date/time picker component, so this
// wraps the plain HTML input by hand. Shared between the Logs page and the
// Tickets page's own date-range filter.
export function DateTimeField({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <Text type="label" size="xsm" color="secondary">
        {label}
      </Text>
      <input
        type="datetime-local"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{
          height: 32,
          padding: '0 10px',
          borderRadius: 'var(--radius-element)',
          border: '1px solid var(--color-border)',
          background: 'var(--color-background-surface)',
          color: 'var(--color-text-primary)',
          fontSize: 13,
          fontFamily: 'inherit',
        }}
      />
    </label>
  )
}
