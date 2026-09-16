"use client";

import { useCallback, useEffect, useRef } from "react";

const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "⌫"];

/**
 * PIN entry that never summons a full keyboard.
 *
 * Two things are going on. The hidden input carries inputMode="numeric" with a
 * numeric pattern, which is what makes iOS and Android open the digit keypad
 * rather than the QWERTY one — a `type="password"` field alone gets the full
 * keyboard, which is the thing being avoided here. And the on-screen pad below
 * means entry works by tapping even when the OS ignores the hint entirely.
 *
 * The dots are the only rendering of the value; the real input stays visually
 * hidden but focusable, so hardware keyboards and password managers still work.
 */
export function PinPad({
  value,
  onChange,
  onComplete,
  length = 4,
  disabled = false,
  autoFocus = true,
}: {
  value: string;
  onChange: (next: string) => void;
  onComplete?: (value: string) => void;
  length?: number;
  disabled?: boolean;
  autoFocus?: boolean;
}) {
  const input = useRef<HTMLInputElement | null>(null);
  const fired = useRef(false);

  useEffect(() => {
    if (autoFocus) input.current?.focus();
  }, [autoFocus]);

  // Submitting the moment the last digit lands is the whole point of a fixed
  // length PIN; the guard stops it firing twice on a re-render.
  useEffect(() => {
    if (value.length === length && !fired.current) {
      fired.current = true;
      onComplete?.(value);
    }
    if (value.length < length) fired.current = false;
  }, [value, length, onComplete]);

  const press = useCallback(
    (key: string) => {
      if (disabled) return;
      if (key === "⌫") {
        onChange(value.slice(0, -1));
        return;
      }
      if (!key || value.length >= length) return;
      onChange(value + key);
    },
    [disabled, length, onChange, value],
  );

  return (
    <div className="flex flex-col items-center gap-6">
      <input
        ref={input}
        // The combination that opens the digit pad on both mobile platforms.
        type="tel"
        inputMode="numeric"
        pattern="[0-9]*"
        autoComplete="one-time-code"
        aria-label={`${length}-digit operator PIN`}
        value={value}
        disabled={disabled}
        maxLength={length}
        onChange={(e) => onChange(e.target.value.replace(/\D/g, "").slice(0, length))}
        className="absolute h-px w-px opacity-0"
      />

      <button
        type="button"
        onClick={() => input.current?.focus()}
        aria-hidden="true"
        tabIndex={-1}
        className="flex items-center gap-4"
      >
        {Array.from({ length }).map((_, i) => (
          <span
            key={i}
            className={`h-3.5 w-3.5 rounded-full border transition-all duration-200 ${
              i < value.length
                ? "scale-110 border-brand bg-brand"
                : "border-hairline bg-transparent"
            }`}
          />
        ))}
      </button>

      <div className="grid w-full max-w-[17rem] grid-cols-3 gap-3">
        {KEYS.map((key, i) =>
          key === "" ? (
            <span key={`gap-${i}`} aria-hidden="true" />
          ) : (
            <button
              key={key}
              type="button"
              onClick={() => press(key)}
              disabled={disabled}
              aria-label={key === "⌫" ? "Delete" : key}
              className="flex h-14 items-center justify-center rounded-full border border-hairline bg-surface-raised text-lg font-medium tabular-nums text-ink transition-colors active:border-brand active:bg-brand-dim disabled:opacity-40"
            >
              {key}
            </button>
          ),
        )}
      </div>
    </div>
  );
}
