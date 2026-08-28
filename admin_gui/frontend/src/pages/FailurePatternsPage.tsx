import { useEffect, useState } from 'react'
import { Button } from '@astryxdesign/core/Button'
import { Dialog } from '@astryxdesign/core/Dialog'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { proportional, Table } from '@astryxdesign/core/Table'
import { Heading, Text } from '@astryxdesign/core/Text'
import { ClickableCell } from '../components/ClickableCell'
import { CodeInline } from '../components/CodePanel'
import { api, type FailurePattern } from '../lib/api'
import { useSortableTable } from '../lib/useSortableTable'
import { FailurePatternForm } from './FailurePatternForm'

export function FailurePatternsPage() {
  const [patterns, setPatterns] = useState<FailurePattern[]>([])
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [isCreating, setIsCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function reload() {
    api.listFailurePatterns().then(setPatterns).catch((err: Error) => setError(err.message))
  }

  useEffect(reload, [])

  function openRow(id: string) {
    setIsCreating(false)
    setExpandedId(id)
  }

  function startCreate() {
    setExpandedId(null)
    setIsCreating(true)
  }

  function close() {
    setExpandedId(null)
    setIsCreating(false)
  }

  async function save(record: FailurePattern) {
    if (isCreating) {
      await api.createFailurePattern(record)
    } else if (expandedId) {
      await api.updateFailurePattern(expandedId, record)
    }
    close()
    reload()
  }

  async function remove(pattern: FailurePattern) {
    await api.deleteFailurePattern(pattern.id)
    if (expandedId === pattern.id) close()
    reload()
  }

  const expandedRecord = patterns.find((p) => p.id === expandedId) ?? null
  const isOpen = expandedRecord !== null || isCreating
  const sortedPatterns = useSortableTable(patterns)

  return (
    <VStack gap={6}>
      <VStack gap={1}>
        <Heading level={1}>Failure-pattern knowledge base</Heading>
        <Text type="body" color="secondary">
          Curated entries `search_failure_patterns` and `cmp://runbook/{'{pattern_id}'}` serve.
        </Text>
      </VStack>
      {error && <Text type="body">{error}</Text>}

      <Table
        data={sortedPatterns.data}
        plugins={sortedPatterns.plugins}
        idKey="id"
        density="compact"
        isStriped
        hasHover
        textOverflow="truncate"
        columns={[
          {
            key: 'id',
            header: 'ID',
            width: proportional(2),
            sortable: true,
            renderCell: (row) => (
              <ClickableCell onClick={() => openRow(row.id)}>
                <CodeInline>{row.id}</CodeInline>
              </ClickableCell>
            ),
          },
          {
            key: 'cause',
            header: 'Cause',
            width: proportional(4),
            sortable: true,
            renderCell: (row) => <ClickableCell onClick={() => openRow(row.id)}>{String(row.cause)}</ClickableCell>,
          },
          {
            key: 'actions',
            header: '',
            width: proportional(1),
            renderCell: (row) => <Button label="Delete" size="sm" variant="ghost" onClick={() => remove(row)} />,
          },
        ]}
      />

      <HStack>
        <Button label="Add pattern" onClick={startCreate} />
      </HStack>

      <Dialog isOpen={isOpen} onOpenChange={(open) => !open && close()} width={640} purpose="form">
        {isOpen && (
          <FailurePatternForm record={isCreating ? null : expandedRecord} onSave={save} onCancel={close} />
        )}
      </Dialog>
    </VStack>
  )
}
