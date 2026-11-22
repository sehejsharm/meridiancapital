/**
 * The dashboard's copy of the last answer to each GET, keyed by URL, with the server's ETag for it.
 *
 * Every poll sends that ETag back; when the server's answer has not changed it replies 304 with no
 * body, and the poll hands back the very object it had before. Same object, so React sees nothing
 * new and redraws nothing. Bounded: the oldest URL is dropped past `max`.
 */
export class EtagCache {
  private readonly entries = new Map<string, { etag: string; data: unknown }>();
  private readonly max: number;

  constructor(max = 120) {
    this.max = max;
  }

  /** The ETag to send with a GET of `url`, if there is a kept answer for it. */
  etag(url: string): string | undefined {
    return this.entries.get(url)?.etag;
  }

  /** The kept answer for `url` (for a 304), or undefined if there is none. */
  data(url: string): unknown {
    const hit = this.entries.get(url);
    if (!hit) return undefined;
    // Re-insert so a URL still being polled is the last one dropped.
    this.entries.delete(url);
    this.entries.set(url, hit);
    return hit.data;
  }

  has(url: string): boolean {
    return this.entries.has(url);
  }

  /** Keeps a 200's answer and ETag; an answer without an ETag replaces nothing and keeps nothing. */
  keep(url: string, etag: string | null, data: unknown): void {
    this.entries.delete(url);
    if (!etag) return;
    this.entries.set(url, { etag, data });
    while (this.entries.size > this.max) {
      const oldest = this.entries.keys().next().value;
      if (oldest === undefined) break;
      this.entries.delete(oldest);
    }
  }

  clear(): void {
    this.entries.clear();
  }
}
