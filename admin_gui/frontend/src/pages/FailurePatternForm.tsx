import { useEffect, useRef, useState } from 'react'
import { Button } from '@astryxdesign/core/Button'
import { DialogHeader } from '@astryxdesign/core/Dialog'
import { Layout, LayoutContent, LayoutFooter } from '@astryxdesign/core/Layout'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { TextArea } from '@astryxdesign/core/TextArea'
import { Text } from '@astryxdesign/core/Text'
import { TextInput } from '@astryxdesign/core/TextInput'
import { type FailurePattern } from '../lib/api'

interface FormState {
  id: string
  signature_pattern: string
  cause: string
  instruction: string
  source_reference: string
}

function toFormState(record: FailurePattern | null): FormState {
  return {
    id: (record?.id as string) ?? '',
    signature_pattern: (record?.signature_pattern as string) ?? '',
    cause: (record?.cause as string) ?? '',
    instruction: (record?.instruction as string) ?? (record?.guidance as string) ?? '',
    source_reference: (record?.source_reference as string) ?? '',
  }
}

function toRecord(form: FormState): FailurePattern {
  return {
    id: form.id,
    signature_pattern: form.signature_pattern,
    cause: form.cause,
    instruction: form.instruction,
    source_reference: form.source_reference,
  } as FailurePattern
}

export function FailurePatternForm({
  record,
  onSave,
  onCancel,
}: {
  record: FailurePattern | null
  onSave: (record: FailurePattern) => Promise<void>
  onCancel: () => void
}) {
  const [form, setForm] = useState<FormState>(toFormState(record))
  const [error, setError] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  const isNew = record === null

  const signatureRef = useRef<HTMLTextAreaElement>(null)
  const causeRef = useRef<HTMLTextAreaElement>(null)
  const instructionRef = useRef<HTMLTextAreaElement>(null)

  const autoGrowTextarea = (el: HTMLTextAreaElement | null) => {
    if (!el) return
    el.style.height = 'auto'
    el.style.height = el.scrollHeight + 'px'
  }

  useEffect(() => {
    autoGrowTextarea(signatureRef.current)
    autoGrowTextarea(causeRef.current)
    autoGrowTextarea(instructionRef.current)
  }, [form])

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }))
  }

  async function save() {
    setError(null)
    setIsSaving(true)
    try {
      await onSave(toRecord(form))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <Layout
      header={
        <DialogHeader
          title={isNew ? 'New pattern' : `Editing ${record.id}`}
          onOpenChange={() => onCancel()}
        />
      }
      content={
        <LayoutContent isScrollable>
          <VStack gap={0}>
            {error && <Text type="body">{error}</Text>}

            <TextInput
              label="ID"
              value={form.id}
              onChange={(v) => set('id', v)}
              isDisabled={!isNew}
              isRequired
            />
            <TextArea
              ref={signatureRef}
              label="Signature pattern"
              description="{placeholder} marks a wildcard part of the error text"
              value={form.signature_pattern}
              onChange={(v) => set('signature_pattern', v)}
              style={{ resize: 'none', overflow: 'hidden' }}
            />
            <TextArea
              ref={causeRef}
              label="Cause"
              value={form.cause}
              onChange={(v) => set('cause', v)}
              style={{ resize: 'none', overflow: 'hidden' }}
            />
            <TextArea
              ref={instructionRef}
              label="Instruction"
              value={form.instruction}
              onChange={(v) => set('instruction', v)}
              style={{ resize: 'none', overflow: 'hidden' }}
            />
            <TextInput
              label="Source reference"
              value={form.source_reference}
              onChange={(v) => set('source_reference', v)}
              isDisabled={!isNew}
              isRequired
            />
          </VStack>
        </LayoutContent>
      }
      footer={
        <LayoutFooter>
          <HStack gap={3} hAlign="end">
            <Button label="Cancel" variant="secondary" onClick={onCancel} />
            <Button label="Save" onClick={save} isLoading={isSaving} />
          </HStack>
        </LayoutFooter>
      }
    />
  )
}
