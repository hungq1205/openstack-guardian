import { api, type EventOut } from './api'

// A ticket summary (`TicketOut`) only carries its latest event's name/status/
// timestamp, not the event's id -- fetching the single most recent event for
// a ticket (`ORDER BY events.id DESC LIMIT 1`, see
// admin_gui/backend/routers/events.py) is the cheap way to get the full
// record `EventDetailDialog` needs to render, without the backend having to
// grow a `latest_event_id` field just for this one click target.
export async function fetchLatestEvent(ticketId: number): Promise<EventOut | null> {
  const events = await api.listEvents({ ticket_id: String(ticketId), limit: 1 })
  return events[0] ?? null
}
