import { useMemo, useState } from 'react'
import { Card } from '@astryxdesign/core/Card'
import { Selector, type SelectorOptionData } from '@astryxdesign/core/Selector'
import { VStack } from '@astryxdesign/core/Stack'
import { Heading, Text } from '@astryxdesign/core/Text'
import { DEFAULT_TIMEZONE, formatTimezoneOffset, getDisplayTimezone, listTimezones, setDisplayTimezone } from '../lib/settings'

// General display preferences for this browser -- currently just the
// timezone every timestamp in the app (tickets, logs, the timeline) is
// rendered in. Local to this machine (see lib/settings.ts) since the app
// itself is a single-operator, no-auth local tool.
export function SettingsPage() {
  const [timezone, setTimezone] = useState(getDisplayTimezone())
  const browserTimezone = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone, [])

  const options: SelectorOptionData[] = useMemo(
    () =>
      listTimezones().map((tz) => ({
        value: tz,
        label: `${tz.replace(/_/g, ' ')} (${formatTimezoneOffset(tz)})`,
      })),
    [],
  )

  function applyTimezone(tz: string) {
    setTimezone(tz)
    setDisplayTimezone(tz)
  }

  return (
    <VStack gap={5}>
      <Heading level={1}>Settings</Heading>

      <Card elevation="low">
        <VStack gap={3}>
          <VStack gap={1}>
            <Heading level={3}>Display timezone</Heading>
            <Text type="body" size="sm" color="secondary">
              Every timestamp in the app -- tickets, logs, the timeline -- is shown in this timezone instead of
              your browser's own. Defaults to {DEFAULT_TIMEZONE}.
            </Text>
          </VStack>

          <Selector
            label="Timezone"
            isLabelHidden
            hasSearch
            searchPlaceholder="Search timezones..."
            options={options}
            value={timezone}
            onChange={(v) => v && applyTimezone(v)}
            width={360}
          />

          {browserTimezone !== timezone && (
            <button
              type="button"
              onClick={() => applyTimezone(browserTimezone)}
              style={{ all: 'unset', cursor: 'pointer', color: 'var(--color-icon-cyan)', fontSize: 12 }}
            >
              Use browser timezone ({browserTimezone.replace(/_/g, ' ')})
            </button>
          )}
        </VStack>
      </Card>
    </VStack>
  )
}
