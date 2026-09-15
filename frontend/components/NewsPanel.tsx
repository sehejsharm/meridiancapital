"use client";

import { Card, Empty } from "@/components/ui";
import { useNews } from "@/lib/useDeskFeeds";

function ago(iso: string | null): string {
  if (!iso) return "";
  const mins = Math.round((Date.now() - Date.parse(iso)) / 60000);
  if (!Number.isFinite(mins)) return "";
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/** Market headlines from public RSS, fetched and deduped by the control plane. */
export function NewsPanel({ limit = 12 }: { limit?: number }) {
  const { data, error } = useNews();
  const items = (data?.items ?? []).slice(0, limit);

  return (
    <Card
      title="Market news"
      subtitle={
        data?.stale
          ? "sources unreachable — showing the last good fetch"
          : data?.fetched_at
            ? `updated ${ago(data.fetched_at)}`
            : "loading…"
      }
    >
      {error && !items.length ? (
        <Empty>News is unreachable right now.</Empty>
      ) : !items.length ? (
        <Empty>No headlines yet.</Empty>
      ) : (
        <ul className="-my-1 divide-y divide-hairline">
          {items.map((item) => (
            <li key={item.link} className="py-2.5">
              <a
                href={item.link}
                target="_blank"
                rel="noreferrer noopener"
                className="group block"
              >
                <p className="text-xs leading-snug text-ink group-hover:text-brand">
                  {item.title}
                </p>
                <p className="mt-1 flex items-center gap-2 text-2xs text-ink-muted">
                  <span className="truncate">{item.source}</span>
                  <span aria-hidden="true">·</span>
                  <span className="shrink-0">{ago(item.published)}</span>
                </p>
              </a>
            </li>
          ))}
        </ul>
      )}
      {data?.errors?.length ? (
        <p className="mt-3 border-t border-hairline pt-2 text-2xs text-warning">
          {data.errors.length} source{data.errors.length === 1 ? "" : "s"} failed this refresh
        </p>
      ) : null}
    </Card>
  );
}
