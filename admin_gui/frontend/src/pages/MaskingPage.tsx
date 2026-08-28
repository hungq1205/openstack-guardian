import { useEffect, useState } from 'react'
import { Badge } from '@astryxdesign/core/Badge'
import { Banner } from '@astryxdesign/core/Banner'
import { Button } from '@astryxdesign/core/Button'
import { Card } from '@astryxdesign/core/Card'
import { Dialog, DialogHeader } from '@astryxdesign/core/Dialog'
import { Layout, LayoutContent } from '@astryxdesign/core/Layout'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Switch } from '@astryxdesign/core/Switch'
import { proportional, Table } from '@astryxdesign/core/Table'
import { Heading, Text } from '@astryxdesign/core/Text'
import { TextInput } from '@astryxdesign/core/TextInput'
import { ClickableCell } from '../components/ClickableCell'
import { CodeInline } from '../components/CodePanel'
import { api, type MaskMatchSample, type MaskingPattern } from '../lib/api'
import { useSortableTable } from '../lib/useSortableTable'

export function MaskingPage() {
  const [patterns, setPatterns] = useState<MaskingPattern[]>([])
  const [newName, setNewName] = useState('')
  const [newRegex, setNewRegex] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [samplesFor, setSamplesFor] = useState<MaskingPattern | null>(null)
  const [samples, setSamples] = useState<MaskMatchSample[]>([])
  const [samplesRevealed, setSamplesRevealed] = useState(false)

  function reload() {
    api.listMaskingPatterns().then(setPatterns).catch((err: Error) => setError(err.message))
  }

  useEffect(reload, [])

  async function addPattern() {
    setError(null)
    try {
      await api.createMaskingPattern(newName, newRegex, 20)
      setNewName('')
      setNewRegex('')
      reload()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function toggleEnabled(pattern: MaskingPattern, enabled: boolean) {
    await api.updateMaskingPattern(pattern.id, { enabled })
    reload()
  }

  async function deletePattern(pattern: MaskingPattern) {
    await api.deleteMaskingPattern(pattern.id)
    reload()
  }

  async function viewSamples(pattern: MaskingPattern) {
    setSamplesFor(pattern)
    setSamplesRevealed(false)
    setSamples(await api.listMaskMatchSamples(pattern.id))
  }

  async function clearSamples() {
    if (!samplesFor) return
    await api.clearMaskMatchSamples(samplesFor.id)
    setSamples([])
  }

  const sortedPatterns = useSortableTable(patterns)
  const sortedSamples = useSortableTable(samples)

  return (
    <VStack gap={3}>
      <VStack gap={0}>
        <Heading level={1}>Masking</Heading>
      </VStack>
      {error && <Text type="body">{error}</Text>}

      <Table
        data={sortedPatterns.data}
        plugins={sortedPatterns.plugins}
        idKey="id"
        isStriped
        hasHover
        columns={[
          {
            key: 'name',
            header: 'Name',
            width: proportional(1),
            sortable: true,
            renderCell: (row) => <ClickableCell onClick={() => viewSamples(row)}>{row.name}</ClickableCell>,
          },
          {
            key: 'regex',
            header: 'Regex',
            width: proportional(2),
            sortable: true,
            renderCell: (row) => (
              <ClickableCell onClick={() => viewSamples(row)}>
                <CodeInline>{row.regex}</CodeInline>
              </ClickableCell>
            ),
          },
          {
            key: 'built_in',
            header: 'Type',
            width: proportional(1),
            sortable: true,
            renderCell: (row) => (
              <ClickableCell onClick={() => viewSamples(row)}>
                <Badge label={row.built_in ? 'built-in' : 'custom'} />
              </ClickableCell>
            ),
          },
          {
            key: 'enabled',
            header: 'Enabled',
            width: proportional(1),
            sortable: true,
            renderCell: (row) => (
              <Switch
                label="Enabled"
                isLabelHidden
                value={row.enabled}
                changeAction={(checked) => toggleEnabled(row, checked)}
              />
            ),
          },
          {
            key: 'actions',
            header: '',
            width: proportional(1),
            renderCell: (row) =>
              !row.built_in && <Button label="Delete" size="sm" variant="ghost" onClick={() => deletePattern(row)} />,
          },
        ]}
      />

      <Card elevation="low">
        <VStack gap={3}>
          <Heading level={3}>Add custom pattern</Heading>
          <HStack gap={3} align="end">
            <TextInput label="Name" value={newName} onChange={setNewName} />
            <TextInput label="Regex" value={newRegex} onChange={setNewRegex} />
            <Button label="Add" onClick={addPattern} isDisabled={!newName || !newRegex} />
          </HStack>
        </VStack>
      </Card>

      <Dialog isOpen={samplesFor !== null} onOpenChange={(open) => !open && setSamplesFor(null)} width={640} purpose="info">
        {samplesFor && (
          <Layout
            header={
              <DialogHeader
                title={`Redacted-match samples for ${samplesFor.name}`}
                onOpenChange={() => setSamplesFor(null)}
              />
            }
            content={
              <LayoutContent isScrollable>
                <VStack gap={3}>
                  <Banner
                    status="warning"
                    title="This stores real redacted text at rest"
                    description="These are real matched values that were redacted -- capped and retention-limited. Reveal only when you need to verify masking is catching the right things."
                  />
                  <HStack gap={3}>
                    <Switch label="Reveal values" value={samplesRevealed} onChange={setSamplesRevealed} />
                    <Button label="Clear samples" variant="secondary" onClick={clearSamples} />
                  </HStack>
                  <Table
                    data={sortedSamples.data}
                    plugins={sortedSamples.plugins}
                    idKey="id"
                    density="compact"
                    isStriped
                    columns={[
                      { key: 'matched_at', header: 'When', width: proportional(1), sortable: true },
                      { key: 'source', header: 'Source', width: proportional(1), sortable: true },
                      {
                        key: 'matched_text',
                        header: 'Matched text',
                        width: proportional(2),
                        sortable: true,
                        renderCell: (row) =>
                          samplesRevealed ? (
                            <CodeInline>{row.matched_text}</CodeInline>
                          ) : (
                            <span
                              aria-label="redacted"
                              style={{
                                display: 'inline-block',
                                width: Math.min(row.matched_text.length, 16) * 7,
                                height: '0.85em',
                                background: 'var(--color-text-primary)',
                                borderRadius: 2,
                              }}
                            />
                          ),
                      },
                    ]}
                  />
                </VStack>
              </LayoutContent>
            }
          />
        )}
      </Dialog>
    </VStack>
  )
}
