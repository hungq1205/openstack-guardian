import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, type EventOut } from './api'

interface PendingApprovalsValue {
  pendingEvents: EventOut[]
  decide: (eventId: number, approved: boolean) => Promise<void>
  requestChanges: (eventId: number, comment: string) => Promise<void>
}

const PendingApprovalsContext = createContext<PendingApprovalsValue | null>(null)

// One persistent SSE connection shared by the whole app, mounted once in
// Shell -- every page sees the same live set of calls awaiting a decision,
// instead of each opening its own subscription. `_event_stream` (see
// admin_gui/backend/routers/events.py) re-emits a still-pending row every
// poll tick regardless of id, so this naturally picks up both brand-new
// pending calls and someone else's decision on an existing one -- no
// separate initial fetch needed.
export function PendingApprovalsProvider({ children }: { children: ReactNode }) {
  const [pendingById, setPendingById] = useState<Map<number, EventOut>>(new Map())

  useEffect(() => {
    const source = new EventSource('/api/events/stream')
    source.addEventListener('log', (message) => {
      const event = JSON.parse((message as MessageEvent<string>).data) as EventOut
      setPendingById((current) => {
        if (event.status !== 'pending') {
          if (!current.has(event.id)) return current
          const next = new Map(current)
          next.delete(event.id)
          return next
        }
        const next = new Map(current)
        next.set(event.id, event)
        return next
      })
    })
    return () => source.close()
  }, [])

  const pendingEvents = useMemo(
    () => Array.from(pendingById.values()).sort((a, b) => a.id - b.id),
    [pendingById],
  )

  function decide(eventId: number, approved: boolean): Promise<void> {
    setPendingById((current) => {
      if (!current.has(eventId)) return current
      const next = new Map(current)
      next.delete(eventId)
      return next
    })
    const request = approved ? api.approveEvent(eventId) : api.denyEvent(eventId)
    // A failed decide (e.g. someone else already decided it -- 409) just
    // means the SSE stream's next tick is the source of truth; nothing
    // more to do here. Callers that need to know once the decision has
    // actually landed (e.g. to refresh a ticket's now-possibly-changed
    // state) can await the returned promise; it never rejects.
    return request.then(() => undefined).catch(() => undefined)
  }

  // A third decision alongside decide() -- rejects this specific submission
  // (shows up as REJECTED, same as a denial) but carries a comment the
  // waiting submit_investigation_plan call reads back out of its own tool
  // result, so the agent can revise and resubmit instead of just stopping.
  function requestChanges(eventId: number, comment: string): Promise<void> {
    setPendingById((current) => {
      if (!current.has(eventId)) return current
      const next = new Map(current)
      next.delete(eventId)
      return next
    })
    return api
      .requestEventChanges(eventId, comment)
      .then(() => undefined)
      .catch(() => undefined)
  }

  return (
    <PendingApprovalsContext.Provider value={{ pendingEvents, decide, requestChanges }}>
      {children}
    </PendingApprovalsContext.Provider>
  )
}

export function usePendingApprovals(): PendingApprovalsValue {
  const value = useContext(PendingApprovalsContext)
  if (value === null) throw new Error('usePendingApprovals must be used within a PendingApprovalsProvider')
  return value
}
