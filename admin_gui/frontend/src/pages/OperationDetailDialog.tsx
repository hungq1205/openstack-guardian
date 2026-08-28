import { useEffect, useState } from 'react'
import { Badge } from '@astryxdesign/core/Badge'
import { Button } from '@astryxdesign/core/Button'
import { DialogHeader } from '@astryxdesign/core/Dialog'
import { Layout, LayoutContent, LayoutFooter } from '@astryxdesign/core/Layout'
import { MultiSelector } from '@astryxdesign/core/MultiSelector'
import { Selector } from '@astryxdesign/core/Selector'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { TextArea } from '@astryxdesign/core/TextArea'
import { Heading, Text } from '@astryxdesign/core/Text'
import { CodeInline } from '../components/CodePanel'
import { api, type OperationDetail } from '../lib/api'

const CATEGORIES = ['action', 'read', 'admin', 'config']
const RISK_LEVELS = ['low', 'medium', 'high']

export function OperationDetailDialog({
  operationId,
  allOperationIds,
  onClose,
}: {
  operationId: string
  allOperationIds: string[]
  onClose: () => void
}) {
  const [detail, setDetail] = useState<OperationDetail | null>(null)
  const [usageNote, setUsageNote] = useState('')
  const [category, setCategory] = useState<string | null>(null)
  const [riskLevel, setRiskLevel] = useState<string | null>(null)
  const [preconditions, setPreconditions] = useState<string[]>([])
  const [relatedTools, setRelatedTools] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)

  useEffect(() => {
    setError(null)
    api
      .getOperationDetail(operationId)
      .then((d) => {
        setDetail(d)
        setUsageNote(d.usage_note)
        setCategory(d.category)
        setRiskLevel(d.risk_level)
        setPreconditions(d.preconditions)
        setRelatedTools(d.related_tools)
      })
      .catch((err: Error) => setError(err.message))
  }, [operationId])

  async function save() {
    setError(null)
    setIsSaving(true)
    try {
      const updated = await api.updateOperationDetail(operationId, {
        usage_note: usageNote,
        category,
        risk_level: riskLevel,
        preconditions,
        related_tools: relatedTools,
      })
      setDetail(updated)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <Layout
      header={<DialogHeader title={operationId} onOpenChange={() => onClose()} />}
      content={
        <LayoutContent isScrollable>
          <VStack gap={3}>
            {error && <Text type="body">{error}</Text>}
            {!detail && !error && <Text type="body">Loading...</Text>}
            {detail && (
              <>
                <CodeInline>
                  {detail.method} {detail.path}
                </CodeInline>
                <Text type="body">{detail.description || detail.summary}</Text>
                <HStack gap={2}>
                  <Badge label={detail.read_only ? 'read-only' : 'mutating'} />
                  {detail.destructive && <Badge label="destructive" />}
                  {detail.idempotent !== null && (
                    <Badge label={detail.idempotent ? 'idempotent' : 'not idempotent'} />
                  )}
                  <Badge label={detail.pinned ? 'pinned' : 'not pinned'} />
                </HStack>

                {detail.parameters.length > 0 && (
                  <VStack gap={1}>
                    <Heading level={4}>Parameters</Heading>
                    {detail.parameters.map((p) => (
                      <Text key={p.name} type="body">
                        {p.name} ({p.location}){p.required ? ', required' : ''}
                      </Text>
                    ))}
                  </VStack>
                )}

                <Heading level={4}>Curated guidance</Heading>
                <HStack gap={3}>
                  <Selector
                    label="Category"
                    options={CATEGORIES}
                    value={category ?? undefined}
                    onChange={(v) => setCategory(v ?? null)}
                  />
                  <Selector
                    label="Risk level"
                    options={RISK_LEVELS}
                    value={riskLevel ?? undefined}
                    onChange={(v) => setRiskLevel(v ?? null)}
                  />
                </HStack>
                <TextArea label="Usage note" value={usageNote} onChange={setUsageNote} rows={3} />
                <MultiSelector
                  label="Related tools"
                  description="Other operations worth knowing about alongside this one"
                  options={allOperationIds}
                  value={relatedTools}
                  onChange={setRelatedTools}
                  hasSearch
                  triggerDisplay="badges"
                />
                <TextArea
                  label="Preconditions"
                  description="One per line"
                  value={preconditions.join('\n')}
                  onChange={(v) => setPreconditions(v.split('\n').map((s) => s.trim()).filter(Boolean))}
                  rows={3}
                />
              </>
            )}
          </VStack>
        </LayoutContent>
      }
      footer={
        <LayoutFooter>
          <HStack gap={3} hAlign="end">
            <Button label="Close" variant="secondary" onClick={onClose} />
            <Button label="Save" onClick={save} isLoading={isSaving} isDisabled={!detail} />
          </HStack>
        </LayoutFooter>
      }
    />
  )
}
