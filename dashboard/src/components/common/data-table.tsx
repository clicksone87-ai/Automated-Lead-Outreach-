"use client";

import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Server-driven table: sorting and pagination state are owned by the caller (URL/query),
 * rows are keyboard-navigable (↑/↓ + Enter) and clickable.
 */
export function DataTable<T>({
  columns,
  data,
  total,
  page,
  pageSize,
  sorting,
  onSortingChange,
  onPageChange,
  onRowClick,
  selectedId,
  getRowId,
  loading,
  empty,
}: {
  columns: ColumnDef<T, unknown>[];
  data: T[];
  total: number;
  page: number;
  pageSize: number;
  sorting: SortingState;
  onSortingChange: (s: SortingState) => void;
  onPageChange: (p: number) => void;
  onRowClick?: (row: T) => void;
  selectedId?: string | null;
  getRowId: (row: T) => string;
  loading?: boolean;
  empty?: React.ReactNode;
}) {
  const table = useReactTable({
    data,
    columns,
    getRowId,
    state: { sorting },
    manualSorting: true,
    manualPagination: true,
    enableSortingRemoval: false,
    onSortingChange: (updater) => onSortingChange(typeof updater === "function" ? updater(sorting) : updater),
    getCoreRowModel: getCoreRowModel(),
  });
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);

  const onKeyDown = (e: React.KeyboardEvent<HTMLTableRowElement>, row: T) => {
    const el = e.currentTarget;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onRowClick?.(row);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      (el.nextElementSibling as HTMLElement | null)?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      (el.previousElementSibling as HTMLElement | null)?.focus();
    }
  };

  return (
    <div className="overflow-hidden rounded-xl border bg-card shadow-soft">
      <div className="scrollbar-thin overflow-x-auto">
        <table className="w-full min-w-[860px] text-sm">
          <thead className="border-b bg-surface-sunken/60">
            {table.getHeaderGroups().map((group) => (
              <tr key={group.id}>
                {group.headers.map((header) => {
                  const canSort = header.column.getCanSort();
                  const dir = header.column.getIsSorted();
                  return (
                    <th
                      key={header.id}
                      scope="col"
                      style={{ width: header.getSize() !== 150 ? header.getSize() : undefined }}
                      className="h-10 px-4 text-left text-xs font-medium text-muted-foreground"
                      aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : undefined}
                    >
                      {canSort ? (
                        <button
                          type="button"
                          onClick={header.column.getToggleSortingHandler()}
                          className="-ml-1 inline-flex items-center gap-1 rounded px-1 py-0.5 hover:text-foreground"
                        >
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {dir === "asc" ? (
                            <ArrowUp className="size-3" />
                          ) : dir === "desc" ? (
                            <ArrowDown className="size-3" />
                          ) : (
                            <ArrowUpDown className="size-3 opacity-40" />
                          )}
                        </button>
                      ) : (
                        flexRender(header.column.columnDef.header, header.getContext())
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody className={cn("transition-opacity", loading && "opacity-60")}>
            {table.getRowModel().rows.map((row) => (
              <tr
                key={row.id}
                tabIndex={0}
                onClick={() => onRowClick?.(row.original)}
                onKeyDown={(e) => onKeyDown(e, row.original)}
                aria-selected={selectedId === row.id}
                className={cn(
                  "cursor-pointer border-b last:border-0 outline-none transition-colors hover:bg-muted/50 focus-visible:bg-primary-soft/50",
                  selectedId === row.id && "bg-primary-soft/60 hover:bg-primary-soft/70",
                )}
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-4 py-3 align-middle">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {data.length === 0 && !loading && empty}
      </div>
      <div className="flex items-center justify-between gap-3 border-t px-4 py-2.5 text-xs text-muted-foreground">
        <span className="tabular">
          {from}–{to} of {total}
        </span>
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" className="size-7" disabled={page <= 1} onClick={() => onPageChange(page - 1)} aria-label="Previous page">
            <ChevronLeft className="size-4" />
          </Button>
          <span className="tabular px-1">
            {page} / {pages}
          </span>
          <Button variant="ghost" size="icon" className="size-7" disabled={page >= pages} onClick={() => onPageChange(page + 1)} aria-label="Next page">
            <ChevronRight className="size-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
