import { useEffect, useState } from 'react'
import { Button } from '@astryxdesign/core/Button'
import { Dialog, DialogHeader } from '@astryxdesign/core/Dialog'
import { Layout, LayoutContent } from '@astryxdesign/core/Layout'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { TextArea } from '@astryxdesign/core/TextArea'
import { Text } from '@astryxdesign/core/Text'
import { api, type CommentOut } from '../lib/api'
import { formatFullTime } from '../lib/time'

// A general-purpose annotation an operator can leave on any logged call --
// unrelated to a plan/report's own request-changes comment (that one lives
// in `error_message` and drives the approval flow; these are free-form
// notes with no effect on anything). One event's comment thread, shown as
// its own small popup rather than folded into the full detail dialog, so
// it's reachable directly from the timeline's own comment-count indicator.
export function CommentsDialog({
  eventId,
  eventLabel,
  onClose,
  onChanged,
}: {
  eventId: number | null
  eventLabel: string
  onClose: () => void
  onChanged: () => void
}) {
  const [comments, setComments] = useState<CommentOut[]>([])
  const [draft, setDraft] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (eventId === null) return
    setError(null)
    api
      .listComments(eventId)
      .then(setComments)
      .catch((err: Error) => setError(err.message))
  }, [eventId])

  function submit() {
    const text = draft.trim()
    if (!text || eventId === null) return
    setIsSending(true)
    setError(null)
    api
      .addComment(eventId, text)
      .then((comment) => {
        setComments((current) => [...current, comment])
        setDraft('')
        onChanged()
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setIsSending(false))
  }

  return (
    <Dialog isOpen={eventId !== null} onOpenChange={(open) => !open && onClose()} width={480} purpose="info">
      {eventId !== null && (
        <Layout
          header={<DialogHeader title="Comments" subtitle={eventLabel} onOpenChange={onClose} />}
          content={
            <LayoutContent isScrollable>
              <VStack gap={4}>
                {comments.length === 0 ? (
                  <Text type="body" color="secondary">
                    No comments yet.
                  </Text>
                ) : (
                  <VStack gap={3}>
                    {comments.map((comment) => (
                      <VStack
                        gap={1}
                        key={comment.id}
                        style={{
                          background: 'var(--color-background-muted)',
                          borderRadius: 'var(--radius-container)',
                          padding: '8px 10px',
                        }}
                      >
                        <Text type="body" style={{ whiteSpace: 'pre-wrap' }}>
                          {comment.text}
                        </Text>
                        <Text type="body" size="xsm" color="secondary">
                          {formatFullTime(comment.created_at)}
                        </Text>
                      </VStack>
                    ))}
                  </VStack>
                )}

                {error && (
                  <Text type="body" style={{ color: 'var(--color-text-red)' }}>
                    {error}
                  </Text>
                )}

                <VStack gap={2}>
                  <TextArea
                    label="Add a comment"
                    isLabelHidden
                    placeholder="Add a comment..."
                    value={draft}
                    onChange={setDraft}
                    rows={3}
                  />
                  <HStack style={{ justifyContent: 'flex-end' }}>
                    <Button label="Comment" onClick={submit} isDisabled={!draft.trim() || isSending} />
                  </HStack>
                </VStack>
              </VStack>
            </LayoutContent>
          }
        />
      )}
    </Dialog>
  )
}
