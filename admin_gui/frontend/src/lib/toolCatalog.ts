import { useEffect, useState } from 'react'
import { api, type OperationSummary, type ToolSummary } from './api'

export interface ToolCatalog {
  descriptions: Record<string, string>
  toolKind: Record<string, 'read' | 'action'>
}

const EMPTY_CATALOG: ToolCatalog = { descriptions: {}, toolKind: {} }

// A tool/operation's read-vs-action classification, keyed by name -- cmp-admin's
// curated category (for spec-backed operations) or the MCP readOnlyHint
// annotation (for everything else: cmp-logs/cmp-notify's tools, and
// cmp-admin's own hand-built ExtraTools -- search_failure_patterns,
// submit_investigation_plan, submit_investigation_report -- which have no
// operation_id and so never appear in `operations` at all). `tools` is
// applied first and `operations` second so the richer curated category wins
// on the (currently nonexistent, but possible) case where a name appears in
// both -- an ExtraTool untouched by that second pass keeps the
// annotation-derived classification from the first.
function buildCatalog(operations: OperationSummary[], tools: ToolSummary[]): ToolCatalog {
  const descriptions: Record<string, string> = {}
  const toolKind: Record<string, 'read' | 'action'> = {}
  for (const tool of tools) {
    descriptions[tool.name] = tool.description
    if (tool.read_only !== null) toolKind[tool.name] = tool.read_only ? 'read' : 'action'
  }
  for (const op of operations) {
    descriptions[op.operation_id] = op.summary
    if (op.category === 'read' || op.category === 'action') toolKind[op.operation_id] = op.category
  }
  return { descriptions, toolKind }
}

// Every tool/operation's agent-facing description plus its read/action
// classification, fetched once and reused wherever a call needs to be
// summarized -- the Logs timeline, the notification toasts, the detail
// dialog. Fetches cmp-admin's own `list_tools()` alongside its full spec
// catalog specifically to cover its ExtraTools -- `listAllAdminOperations()`
// only walks the OpenAPI-derived operation set, so an ExtraTool is invisible
// to it no matter how the catalog is built.
export function useToolCatalog(): ToolCatalog {
  const [catalog, setCatalog] = useState<ToolCatalog>(EMPTY_CATALOG)

  useEffect(() => {
    Promise.all([
      api.listAllAdminOperations(),
      api.listTools('cmp-admin'),
      api.listTools('cmp-logs'),
      api.listTools('cmp-notify'),
    ])
      .then(([operations, adminTools, logsTools, notifyTools]) => {
        setCatalog(buildCatalog(operations, [...adminTools, ...logsTools, ...notifyTools]))
      })
      .catch(() => undefined)
  }, [])

  return catalog
}
