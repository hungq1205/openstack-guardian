import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Dialog } from '@astryxdesign/core/Dialog'
import { Selector } from '@astryxdesign/core/Selector'
import { VStack } from '@astryxdesign/core/Stack'
import { Switch } from '@astryxdesign/core/Switch'
import { proportional, Table, useTableRowStatus } from '@astryxdesign/core/Table'
import { Heading, Text } from '@astryxdesign/core/Text'
import { ClickableCell } from '../components/ClickableCell'
import { CodeInline } from '../components/CodePanel'
import { useSortableTable } from '../lib/useSortableTable'
import {
  api,
  type OperationSummary,
  type PromptSummary,
  type ResourceTemplateSummary,
  type ToolSummary,
} from '../lib/api'
import { OperationDetailDialog } from './OperationDetailDialog'

const SERVERS = ['cmp-admin', 'cmp-logs', 'cmp-notify']

export function CatalogPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedServer = searchParams.get('server')
  const [serverId, setServerId] = useState(
    requestedServer && SERVERS.includes(requestedServer) ? requestedServer : 'cmp-admin',
  )

  function selectServer(value: string) {
    setServerId(value)
    setSearchParams(value === 'cmp-admin' ? {} : { server: value })
  }
  const [tools, setTools] = useState<ToolSummary[]>([])
  const [operations, setOperations] = useState<OperationSummary[]>([])
  const [resources, setResources] = useState<ResourceTemplateSummary[]>([])
  const [prompts, setPrompts] = useState<PromptSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [selectedOperationId, setSelectedOperationId] = useState<string | null>(null)
  const [jumpedToOperationId, setJumpedToOperationId] = useState<string | null>(null)

  function reload() {
    setError(null)
    Promise.all([
      api.listTools(serverId),
      api.listResourceTemplates(serverId),
      api.listPrompts(serverId),
      serverId === 'cmp-admin' ? api.listAllAdminOperations() : Promise.resolve([]),
    ])
      .then(([toolsResult, resourcesResult, promptsResult, operationsResult]) => {
        setTools(toolsResult)
        setResources(resourcesResult)
        setPrompts(promptsResult)
        setOperations(operationsResult)
      })
      .catch((err: Error) => setError(err.message))
  }

  useEffect(reload, [serverId])

  function closeDetail() {
    setSelectedOperationId(null)
    reload()
  }

  async function togglePinned(operationId: string, pinned: boolean) {
    await api.setPinned(operationId, pinned)
    setOperations((current) =>
      current.map((op) => (op.operation_id === operationId ? { ...op, pinned } : op)),
    )
  }

  function scrollToOperation(operationId: string) {
    document.getElementById(`op-row-${operationId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setJumpedToOperationId(operationId)
    setTimeout(() => setJumpedToOperationId((current) => (current === operationId ? null : current)), 1800)
  }

  const getRowStatus = useCallback(
    (row: OperationSummary) =>
      row.operation_id === jumpedToOperationId
        ? { color: 'accent' as const, label: 'Jumped to this operation from Pinned tools' }
        : null,
    [jumpedToOperationId],
  )
  const rowStatus = useTableRowStatus<OperationSummary>({ getStatus: getRowStatus })

  const sortedOperations = useSortableTable(operations)
  const sortedResources = useSortableTable(resources)
  const sortedPrompts = useSortableTable(prompts)

  return (
    <VStack gap={6}>
      <VStack gap={0}>
        <Heading level={1}>Catalog</Heading>
      </VStack>
      <Selector
        label="Server"
        options={SERVERS}
        value={serverId}
        onChange={(value) => value && selectServer(value)}
      />
      {error && <Text type="body">Failed to load catalog: {error}</Text>}

      <VStack gap={2}>
        <Heading level={2}>Pinned tools ({tools.length})</Heading>
        {tools.length === 0 ? (
          <Text type="body" color="secondary">
            No pinned tools.
          </Text>
        ) : (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {tools.map((tool) => {
              const isJumpable = serverId === 'cmp-admin' && operations.some((op) => op.operation_id === tool.name)
              return (
                <div
                  key={tool.name}
                  title={tool.description}
                  onClick={isJumpable ? () => scrollToOperation(tool.name) : undefined}
                  style={{
                    padding: '8px 14px',
                    border: '1px solid var(--color-border)',
                    borderRadius: 'var(--radius-container)',
                    background: 'var(--color-background-card)',
                    fontFamily: 'var(--font-family-code)',
                    fontSize: 13,
                    fontWeight: 600,
                    cursor: isJumpable ? 'pointer' : 'default',
                  }}
                >
                  {tool.name}
                </div>
              )
            })}
          </div>
        )}
      </VStack>

      {serverId === 'cmp-admin' && (
        <VStack gap={2}>
          <Heading level={2}>All operations ({operations.length})</Heading>
          <Table
            data={sortedOperations.data}
            plugins={{ ...sortedOperations.plugins, rowStatus }}
            idKey="operation_id"
            density="compact"
            isStriped
            columns={[
              {
                key: 'operation_id',
                header: 'Operation',
                width: proportional(2),
                sortable: true,
                renderCell: (row) => (
                  <div id={`op-row-${row.operation_id}`}>
                    <ClickableCell onClick={() => setSelectedOperationId(row.operation_id)}>
                      <CodeInline>{row.operation_id}</CodeInline>
                    </ClickableCell>
                  </div>
                ),
              },
              {
                key: 'category',
                header: 'Category',
                width: proportional(1),
                sortable: true,
                renderCell: (row) => (
                  <ClickableCell onClick={() => setSelectedOperationId(row.operation_id)}>
                    {row.category ?? '—'}
                  </ClickableCell>
                ),
              },
              {
                key: 'risk_level',
                header: 'Risk',
                width: proportional(1),
                sortable: true,
                renderCell: (row) => (
                  <ClickableCell onClick={() => setSelectedOperationId(row.operation_id)}>
                    {row.risk_level ?? '—'}
                  </ClickableCell>
                ),
              },
              {
                key: 'pinned',
                header: 'Pinned',
                width: proportional(1),
                sortable: true,
                renderCell: (row) => (
                  <Switch
                    label="Pinned"
                    isLabelHidden
                    value={row.pinned}
                    changeAction={(checked) => togglePinned(row.operation_id, checked)}
                  />
                ),
              },
            ]}
          />
        </VStack>
      )}

      <VStack gap={2}>
        <Heading level={2}>Resource templates</Heading>
        <Table
          data={sortedResources.data}
          plugins={sortedResources.plugins}
          idKey="uri_template"
          isStriped
          columns={[
            {
              key: 'uri_template',
              header: 'URI template',
              width: proportional(1),
              sortable: true,
              renderCell: (row) => <CodeInline>{row.uri_template}</CodeInline>,
            },
            { key: 'description', header: 'Description', width: proportional(2), sortable: true },
          ]}
        />
      </VStack>

      <VStack gap={2}>
        <Heading level={2}>Prompts</Heading>
        <Table
          data={sortedPrompts.data}
          plugins={sortedPrompts.plugins}
          idKey="name"
          isStriped
          columns={[
            { key: 'name', header: 'Name', width: proportional(1), sortable: true },
            { key: 'description', header: 'Description', width: proportional(2), sortable: true },
          ]}
        />
      </VStack>

      <Dialog isOpen={selectedOperationId !== null} onOpenChange={(open) => !open && closeDetail()} width={640} purpose="form">
        {selectedOperationId && (
          <OperationDetailDialog
            operationId={selectedOperationId}
            allOperationIds={operations.map((op) => op.operation_id)}
            onClose={closeDetail}
          />
        )}
      </Dialog>
    </VStack>
  )
}
