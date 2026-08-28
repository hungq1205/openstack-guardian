import { useEffect, useState } from 'react'
import { Banner } from '@astryxdesign/core/Banner'
import { Button } from '@astryxdesign/core/Button'
import { Card } from '@astryxdesign/core/Card'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Heading } from '@astryxdesign/core/Text'
import { TextInput } from '@astryxdesign/core/TextInput'
import { api, type ConnectionFields, type TestConnectionResult } from '../lib/api'

interface FieldSpec {
  key: string
  label: string
  isSecret?: boolean
}

interface ConnectionSpec {
  id: string
  title: string
  fields: FieldSpec[]
}

const CONNECTIONS: ConnectionSpec[] = [
  {
    id: 'cmp-admin-v2',
    title: 'CMP admin-v2 API',
    fields: [
      { key: 'base_url', label: 'Base URL' },
      { key: 'pat', label: 'Personal access token', isSecret: true },
      { key: 'username', label: 'Username' },
      { key: 'password', label: 'Password', isSecret: true },
    ],
  },
  {
    id: 'elasticsearch',
    title: 'Elasticsearch (cmp-logs)',
    fields: [
      { key: 'url', label: 'URL' },
      { key: 'index', label: 'Index' },
      { key: 'api_key', label: 'API key', isSecret: true },
      { key: 'username', label: 'Username' },
      { key: 'password', label: 'Password', isSecret: true },
    ],
  },
  {
    id: 'notify',
    title: 'Notify webhook (cmp-notify)',
    fields: [{ key: 'webhook_url', label: 'Webhook URL', isSecret: true }],
  },
]

function ConnectionCard({ spec }: { spec: ConnectionSpec }) {
  const [fields, setFields] = useState<ConnectionFields>({})
  const [edits, setEdits] = useState<ConnectionFields>({})
  const [testResult, setTestResult] = useState<TestConnectionResult | null>(null)
  const [isTesting, setIsTesting] = useState(false)
  const [isSaving, setIsSaving] = useState(false)

  function reload() {
    api.getConnection(spec.id).then(setFields)
  }

  useEffect(reload, [spec.id])

  async function save() {
    setIsSaving(true)
    try {
      await api.updateConnection(spec.id, edits)
      setEdits({})
      reload()
    } finally {
      setIsSaving(false)
    }
  }

  async function test() {
    setIsTesting(true)
    setTestResult(null)
    try {
      setTestResult(await api.testConnection(spec.id, { ...fields, ...edits }))
    } finally {
      setIsTesting(false)
    }
  }

  return (
    <Card elevation="low">
      <VStack gap={3}>
        <Heading level={3}>{spec.title}</Heading>
        {spec.fields.map((field) => (
          <TextInput
            key={field.key}
            label={field.label}
            type={field.isSecret ? 'password' : 'text'}
            value={edits[field.key] ?? fields[field.key] ?? ''}
            onChange={(value) => setEdits((current) => ({ ...current, [field.key]: value }))}
            placeholder={field.isSecret ? 'unchanged unless edited' : undefined}
          />
        ))}
        <HStack gap={3}>
          <Button label="Save" onClick={save} isLoading={isSaving} isDisabled={Object.keys(edits).length === 0} />
          <Button label="Test connection" variant="secondary" onClick={test} isLoading={isTesting} />
        </HStack>
        {testResult && (
          <Banner
            status={testResult.reachable ? 'success' : 'error'}
            title={testResult.reachable ? 'Reachable' : 'Not reachable'}
            description={testResult.message}
          />
        )}
      </VStack>
    </Card>
  )
}

export function ConnectionsPage() {
  return (
    <VStack gap={3}>
      <VStack gap={0}>
        <Heading level={1}>Connections</Heading>
      </VStack>
      {CONNECTIONS.map((spec) => (
        <ConnectionCard key={spec.id} spec={spec} />
      ))}
    </VStack>
  )
}
