import { useRef, useState } from 'react'
import { Button } from '@astryxdesign/core/Button'
import { DialogHeader } from '@astryxdesign/core/Dialog'
import { Layout, LayoutContent, LayoutFooter } from '@astryxdesign/core/Layout'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Text } from '@astryxdesign/core/Text'
import { TextInput } from '@astryxdesign/core/TextInput'
import { api, type SpecSource } from '../lib/api'

function fileName(path: string): string {
  return path.split(/[\\/]/).pop() || path
}

export function SpecSourceEditDialog({
  server,
  source,
  onClose,
}: {
  server: string
  source: SpecSource
  onClose: () => void
}) {
  const [name, setName] = useState(source.name)
  const [error, setError] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  const specFileRef = useRef<HTMLInputElement>(null)
  const annotationsFileRef = useRef<HTMLInputElement>(null)

  async function save() {
    setError(null)
    setIsSaving(true)
    try {
      await api.updateBuiltInSpecSource(server, source.domain as string, {
        name,
        specFile: specFileRef.current?.files?.[0] ?? null,
        annotationsFile: annotationsFileRef.current?.files?.[0] ?? null,
      })
      onClose()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <Layout
      header={<DialogHeader title={`Editing "${source.domain}"`} onOpenChange={() => onClose()} />}
      content={
        <LayoutContent isScrollable>
          <VStack gap={3}>
            {error && <Text type="body">{error}</Text>}
            <TextInput label="Name" value={name} onChange={setName} />
            <VStack gap={1}>
              <Text type="body">Spec JSON -- currently {fileName(source.spec_path)}</Text>
              <input ref={specFileRef} type="file" accept="application/json" />
            </VStack>
            <VStack gap={1}>
              <Text type="body">
                Annotations JSON --{' '}
                {source.annotations_path ? `currently ${fileName(source.annotations_path)}` : 'none'}
              </Text>
              <input ref={annotationsFileRef} type="file" accept="application/json" />
            </VStack>
            <Text type="body">Leave a file input empty to keep the current file.</Text>
          </VStack>
        </LayoutContent>
      }
      footer={
        <LayoutFooter>
          <HStack gap={3} hAlign="end">
            <Button label="Cancel" variant="secondary" onClick={onClose} />
            <Button label="Save" onClick={save} isLoading={isSaving} />
          </HStack>
        </LayoutFooter>
      }
    />
  )
}
