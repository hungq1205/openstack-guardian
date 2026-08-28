import type { ReactNode } from 'react'

// Wraps a Table cell's rendered content so clicking anywhere in the cell
// (not just a nested button) opens a row's detail dialog -- Table has no
// built-in onRowClick, so every non-action column in a clickable-row table
// gets wrapped in one of these instead.
export function ClickableCell({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <div onClick={onClick} style={{ cursor: 'pointer', width: '100%' }}>
      {children}
    </div>
  )
}
