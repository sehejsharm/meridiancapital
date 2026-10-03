import { Logo } from "@/components/Logo";

/**
 * The mark, faint and fixed behind the reading.
 *
 * It sits over the page rather than under it — the cards are opaque, so
 * underneath it would only show in the gutters — but at a few percent opacity
 * and with no pointer events, so it tints nothing enough to cost legibility
 * and never takes a click. The header, menus and dialogs stay above it.
 */
export function Watermark() {
  return (
    <div className="watermark" aria-hidden="true">
      <Logo size={400} withFrame={false} className="watermark-mark text-brand" />
    </div>
  );
}
