import { useTableSortable, useTableSortableState } from '@astryxdesign/core/Table'

// Wraps Astryx's two-piece sortable-table API (useTableSortableState +
// useTableSortable) into one call every page's Table can use the same way:
// pass `sorted.data` as the Table's `data` prop and `sorted.plugins` as its
// `plugins` prop, and mark each column `sortable: true`.
export function useSortableTable<T extends Record<string, unknown>>(data: T[]) {
  const { sortedData, sortConfig } = useTableSortableState<T>({ data })
  const sortable = useTableSortable<T>(sortConfig)
  return { data: sortedData, plugins: { sortable } }
}
