import { useEffect, useRef, useState } from 'react'
import { Badge } from '@astryxdesign/core/Badge'
import { Button } from '@astryxdesign/core/Button'
import { Card } from '@astryxdesign/core/Card'
import { Dialog } from '@astryxdesign/core/Dialog'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Switch } from '@astryxdesign/core/Switch'
import { proportional, Table } from '@astryxdesign/core/Table'
import { Heading, Text } from '@astryxdesign/core/Text'
import { TextInput } from '@astryxdesign/core/TextInput'
import { ClickableCell } from '../components/ClickableCell'
import { CodeInline } from '../components/CodePanel'
import { api, type SpecSource } from '../lib/api'
import { useSortableTable } from '../lib/useSortableTable'
import { SpecSourceEditDialog } from './SpecSourceEditDialog'

const SERVER = 'cmp-admin'

function fileName(path: string): string {
  return path.split(/[\\/]/).pop() || path
}

export function SpecSourcesPage() {
  const [sources, setSources] = useState<SpecSource[]>([])
  const [error, setError] = useState<string | null>(null)
  const [isUploading, setIsUploading] = useState(false)
  const [newName, setNewName] = useState('')
  const [editingDomain, setEditingDomain] = useState<string | null>(null)
  const specFileRef = useRef<HTMLInputElement>(null)
  const annotationsFileRef = useRef<HTMLInputElement>(null)

  function reload() {
    api.listSpecSources(SERVER).then(setSources).catch((err: Error) => setError(err.message))
  }

  useEffect(reload, [])

  async function toggleEnabled(source: SpecSource, enabled: boolean) {
    if (source.domain) {
      await api.updateBuiltInSpecSource(SERVER, source.domain, { enabled })
    } else if (source.id !== null) {
      await api.setSpecSourceEnabled(source.id, enabled)
    }
    reload()
  }

  async function remove(source: SpecSource) {
    if (source.id !== null) await api.deleteSpecSource(source.id)
    reload()
  }

  async function upload() {
    if (!newName.trim()) {
      setError('give this spec source a name first')
      return
    }
    const specFile = specFileRef.current?.files?.[0]
    if (!specFile) {
      setError('choose a spec JSON file first')
      return
    }
    const annotationsFile = annotationsFileRef.current?.files?.[0] ?? null
    setIsUploading(true)
    setError(null)
    try {
      await api.addSpecSource(SERVER, newName.trim(), specFile, annotationsFile)
      setNewName('')
      if (specFileRef.current) specFileRef.current.value = ''
      if (annotationsFileRef.current) annotationsFileRef.current.value = ''
      reload()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setIsUploading(false)
    }
  }

  function closeEdit() {
    setEditingDomain(null)
    reload()
  }

  const sortedSources = useSortableTable(sources)
  const editingSource = sources.find((s) => s.domain === editingDomain) ?? null

  return (
    <VStack gap={3}>
      <VStack gap={0}>
        <Heading level={1}>Spec sources</Heading>
      </VStack>
      {error && <Text type="body">{error}</Text>}

      <Table
        data={sortedSources.data}
        plugins={sortedSources.plugins}
        idKey={(row) => (row.id !== null ? `id-${row.id}` : `domain-${row.domain}`)}
        isStriped
        hasHover
        columns={[
          {
            key: 'name',
            header: 'Name',
            width: proportional(1),
            sortable: true,
            renderCell: (row) =>
              row.domain ? (
                <ClickableCell onClick={() => setEditingDomain(row.domain)}>{row.name}</ClickableCell>
              ) : (
                row.name
              ),
          },
          {
            key: 'spec_path',
            header: 'Spec file',
            width: proportional(2),
            sortable: true,
            renderCell: (row) =>
              row.domain ? (
                <ClickableCell onClick={() => setEditingDomain(row.domain)}>
                  <CodeInline>{fileName(row.spec_path)}</CodeInline>
                </ClickableCell>
              ) : (
                <CodeInline>{fileName(row.spec_path)}</CodeInline>
              ),
          },
          {
            key: 'annotations_path',
            header: 'Annotations file',
            width: proportional(2),
            sortable: true,
            renderCell: (row) => {
              if (!row.annotations_path) return '—'
              const chip = <CodeInline>{fileName(row.annotations_path)}</CodeInline>
              return row.domain ? (
                <ClickableCell onClick={() => setEditingDomain(row.domain)}>{chip}</ClickableCell>
              ) : (
                chip
              )
            },
          },
          {
            key: 'domain',
            header: 'Type',
            width: proportional(1),
            sortable: true,
            renderCell: (row) => {
              const badge = <Badge label={row.domain ? 'built-in' : 'additive'} />
              return row.domain ? (
                <ClickableCell onClick={() => setEditingDomain(row.domain)}>{badge}</ClickableCell>
              ) : (
                badge
              )
            },
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
              row.domain ? (
                row.id !== null && (
                  <Button label="Reset to default" size="sm" variant="ghost" onClick={() => remove(row)} />
                )
              ) : (
                <Button label="Remove" size="sm" variant="ghost" onClick={() => remove(row)} />
              ),
          },
        ]}
      />

      <Card elevation="low">
        <VStack gap={3}>
          <Heading level={3}>Add an extra spec source</Heading>
          <TextInput
            label="Name"
            value={newName}
            onChange={setNewName}
          />
          <VStack gap={1}>
            <Text type="body">Spec JSON (required)</Text>
            <input ref={specFileRef} type="file" accept="application/json" />
          </VStack>
          <VStack gap={1}>
            <Text type="body">Annotations JSON (optional)</Text>
            <input ref={annotationsFileRef} type="file" accept="application/json" />
          </VStack>
          <HStack>
            <Button label="Upload" onClick={upload} isLoading={isUploading} />
          </HStack>
        </VStack>
      </Card>

      <Dialog isOpen={editingSource !== null} onOpenChange={(open) => !open && closeEdit()} width={560} purpose="form">
        {editingSource && <SpecSourceEditDialog server={SERVER} source={editingSource} onClose={closeEdit} />}
      </Dialog>
    </VStack>
  )
}
