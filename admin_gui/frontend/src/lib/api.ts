// Typed fetch helpers for the admin GUI backend (admin_gui/backend). Every
// call goes through the dev-time Vite proxy (vite.config.ts) or, in
// production, the same FastAPI process that serves this built frontend --
// so a bare `/api/...` path is always correct.

// `extends Record<string, unknown>` on every row-shaped type below is what
// `@astryxdesign/core/Table`'s `data` prop requires (its own docs: "T must
// extend Record<string, unknown>") -- not just a TypeScript nicety.

export interface ServerSummary extends Record<string, unknown> {
  id: string
  description: string
}

export interface ToolSummary extends Record<string, unknown> {
  name: string
  description: string
  read_only: boolean | null
  destructive: boolean | null
}

export interface OperationSummary extends Record<string, unknown> {
  operation_id: string
  summary: string
  category: string | null
  risk_level: string | null
  read_only: boolean
  destructive: boolean
  pinned: boolean
}

export interface OperationParameter extends Record<string, unknown> {
  name: string
  location: string
  required: boolean
  schema: Record<string, unknown>
}

export interface OperationDetail extends Record<string, unknown> {
  operation_id: string
  method: string
  path: string
  summary: string
  description: string
  parameters: OperationParameter[]
  body_schema: Record<string, unknown> | null
  body_required: boolean
  output_schema: Record<string, unknown> | null
  usage_note: string
  category: string | null
  risk_level: string | null
  preconditions: string[]
  related_tools: string[]
  read_only: boolean
  destructive: boolean
  idempotent: boolean | null
  pinned: boolean
}

export interface OperationDetailUpdate {
  usage_note?: string
  category?: string | null
  risk_level?: string | null
  preconditions?: string[]
  related_tools?: string[]
  read_only?: boolean | null
  destructive?: boolean | null
  idempotent?: boolean | null
}

export interface ResourceTemplateSummary extends Record<string, unknown> {
  uri_template: string
  name: string
  description: string
}

export interface PromptSummary extends Record<string, unknown> {
  name: string
  description: string | null
}

export interface EventOut extends Record<string, unknown> {
  id: number
  ts: string
  server: string
  kind: string
  name: string
  arguments_json: string
  status: string
  error_message: string | null
  result_summary: string
  duration_ms: number
  pid: number
  action: string | null
  ticket_id: number | null
  comment_count: number
}

export interface CommentOut extends Record<string, unknown> {
  id: number
  event_id: number
  text: string
  created_at: string
}

export interface TicketOut extends Record<string, unknown> {
  id: number
  session_id: string
  title: string
  initial_prompt: string | null
  resource_id: string | null
  state: string
  created_at: string
  closed_at: string | null
  deleted_at: string | null
  event_count: number
  comment_count: number
  latest_event_name: string | null
  latest_event_status: string | null
  latest_event_ts: string | null
  latest_event_action: string | null
}

export interface TicketUpdate {
  title?: string
}

export interface TicketTransitionOut extends Record<string, unknown> {
  id: number
  ticket_id: number
  ticket_title: string
  from_state: string | null
  to_state: string
  ts: string
}

export interface ConnectionFields {
  [field: string]: string | undefined
}

export interface TestConnectionResult {
  reachable: boolean
  status_code?: number
  message?: string
}

export interface MaskingPattern extends Record<string, unknown> {
  id: number
  name: string
  regex: string
  enabled: boolean
  built_in: boolean
  sample_retention_count: number
}

export interface MaskMatchSample extends Record<string, unknown> {
  id: number
  matched_at: string
  matched_text: string
  source: string
}

export interface SpecSource extends Record<string, unknown> {
  id: number | null
  server: string
  name: string
  domain: string | null
  spec_path: string
  annotations_path: string | null
  enabled: boolean
  sort_order: number
}

export type FailurePattern = Record<string, unknown> & { id: string }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: init?.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  })
  if (!response.ok) {
    const body = await response.text()
    let detail = body
    try {
      detail = JSON.parse(body).detail ?? body
    } catch {
      // not JSON, use raw body
    }
    throw new Error(`${response.status} ${response.statusText}: ${detail}`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

const json = (body: unknown) => JSON.stringify(body)

export const api = {
  listServers: () => request<ServerSummary[]>('/api/servers'),
  listTools: (serverId: string) => request<ToolSummary[]>(`/api/servers/${serverId}/tools`),
  listAllAdminOperations: () => request<OperationSummary[]>('/api/servers/cmp-admin/tools/all'),
  setPinned: (operationId: string, pinned: boolean) =>
    request<{ pinned: boolean }>(`/api/servers/cmp-admin/tools/${operationId}/pin`, {
      method: 'POST',
      body: json({ pinned }),
    }),
  getOperationDetail: (operationId: string) =>
    request<OperationDetail>(`/api/servers/cmp-admin/tools/${operationId}`),
  updateOperationDetail: (operationId: string, update: OperationDetailUpdate) =>
    request<OperationDetail>(`/api/servers/cmp-admin/tools/${operationId}`, {
      method: 'PUT',
      body: json(update),
    }),
  listResourceTemplates: (serverId: string) =>
    request<ResourceTemplateSummary[]>(`/api/servers/${serverId}/resources`),
  listPrompts: (serverId: string) => request<PromptSummary[]>(`/api/servers/${serverId}/prompts`),

  listEvents: (filters: {
    server?: string
    kind?: string
    status?: string
    ticket_id?: string
    ticket_state?: string
    since?: string
    until?: string
    limit?: number
  }) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== '') params.set(key, String(value))
    }
    return request<EventOut[]>(`/api/events?${params.toString()}`)
  },
  approveEvent: (eventId: number) => request<{ status: string }>(`/api/events/${eventId}/approve`, { method: 'POST' }),
  denyEvent: (eventId: number) => request<{ status: string }>(`/api/events/${eventId}/deny`, { method: 'POST' }),
  requestEventChanges: (eventId: number, comment: string) =>
    request<{ status: string }>(`/api/events/${eventId}/request-changes`, {
      method: 'POST',
      body: json({ comment }),
    }),
  clearEvents: () => request<{ deleted: number }>('/api/events', { method: 'DELETE' }),
  listComments: (eventId: number) => request<CommentOut[]>(`/api/events/${eventId}/comments`),
  addComment: (eventId: number, text: string) =>
    request<CommentOut>(`/api/events/${eventId}/comments`, { method: 'POST', body: json({ text }) }),

  listTickets: (opts?: { trashed?: boolean }) =>
    request<TicketOut[]>(`/api/tickets${opts?.trashed ? '?trashed=true' : ''}`),
  getTicket: (id: number) => request<TicketOut>(`/api/tickets/${id}`),
  updateTicket: (id: number, update: TicketUpdate) =>
    request<TicketOut>(`/api/tickets/${id}`, { method: 'PATCH', body: json(update) }),
  trashTicket: (id: number) => request<TicketOut>(`/api/tickets/${id}/trash`, { method: 'POST' }),
  restoreTicket: (id: number) => request<TicketOut>(`/api/tickets/${id}/restore`, { method: 'POST' }),
  listTicketTransitions: (limit?: number) =>
    request<TicketTransitionOut[]>(`/api/tickets/transitions${limit ? `?limit=${limit}` : ''}`),
  deleteTicket: (id: number) =>
    request<{ detached_events: number }>(`/api/tickets/${id}`, { method: 'DELETE' }),

  getConnection: (connection: string) => request<ConnectionFields>(`/api/config/${connection}`),
  updateConnection: (connection: string, fields: ConnectionFields) =>
    request<ConnectionFields>(`/api/config/${connection}`, { method: 'PUT', body: json(fields) }),
  testConnection: (connection: string, fields: ConnectionFields) =>
    request<TestConnectionResult>(`/api/config/${connection}/test-connection`, {
      method: 'POST',
      body: json(fields),
    }),

  listMaskingPatterns: () => request<MaskingPattern[]>('/api/masking/patterns'),
  createMaskingPattern: (name: string, regex: string, sampleRetentionCount: number) =>
    request<MaskingPattern>('/api/masking/patterns', {
      method: 'POST',
      body: json({ name, regex, sample_retention_count: sampleRetentionCount }),
    }),
  updateMaskingPattern: (
    id: number,
    update: { regex?: string; enabled?: boolean; sample_retention_count?: number },
  ) => request<MaskingPattern>(`/api/masking/patterns/${id}`, { method: 'PUT', body: json(update) }),
  deleteMaskingPattern: (id: number) =>
    request<void>(`/api/masking/patterns/${id}`, { method: 'DELETE' }),
  listMaskMatchSamples: (patternId: number) =>
    request<MaskMatchSample[]>(`/api/masking/patterns/${patternId}/samples`),
  clearMaskMatchSamples: (patternId: number) =>
    request<void>(`/api/masking/patterns/${patternId}/samples`, { method: 'DELETE' }),

  listSpecSources: (server: string) => request<SpecSource[]>(`/api/spec-sources?server=${server}`),
  addSpecSource: (server: string, name: string, specFile: File, annotationsFile: File | null) => {
    const form = new FormData()
    form.set('server', server)
    form.set('name', name)
    form.set('spec_file', specFile)
    if (annotationsFile) form.set('annotations_file', annotationsFile)
    return request<SpecSource>('/api/spec-sources', { method: 'POST', body: form })
  },
  updateBuiltInSpecSource: (
    server: string,
    domain: string,
    update: { name?: string; enabled?: boolean; specFile?: File | null; annotationsFile?: File | null },
  ) => {
    const form = new FormData()
    form.set('server', server)
    if (update.name !== undefined) form.set('name', update.name)
    if (update.enabled !== undefined) form.set('enabled', String(update.enabled))
    if (update.specFile) form.set('spec_file', update.specFile)
    if (update.annotationsFile) form.set('annotations_file', update.annotationsFile)
    return request<SpecSource>(`/api/spec-sources/built-in/${domain}`, { method: 'PUT', body: form })
  },
  setSpecSourceEnabled: (id: number, enabled: boolean) =>
    request<{ enabled: boolean }>(`/api/spec-sources/${id}`, { method: 'PUT', body: json({ enabled }) }),
  deleteSpecSource: (id: number) => request<void>(`/api/spec-sources/${id}`, { method: 'DELETE' }),

  listFailurePatterns: () => request<FailurePattern[]>('/api/failure-patterns'),
  createFailurePattern: (record: FailurePattern) =>
    request<FailurePattern>('/api/failure-patterns', { method: 'POST', body: json(record) }),
  updateFailurePattern: (patternId: string, record: FailurePattern) =>
    request<FailurePattern>(`/api/failure-patterns/${patternId}`, { method: 'PUT', body: json(record) }),
  deleteFailurePattern: (patternId: string) =>
    request<void>(`/api/failure-patterns/${patternId}`, { method: 'DELETE' }),
}
