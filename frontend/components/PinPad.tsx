"use client";

import { useCallback, useEffect, useRef } from "react";

const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "⌫"];

/**
 * Numeric entry that never summons a full keyboard.
 *
 * The hidden input carries type="tel" with inputMode="numeric", which is what
 * makes iOS and Android open the digit pad — a type="password" field gets
 * QWERTY. The on-screen pad below means entry still works by tapping when the
 * OS ignores the hint, and the real input stays focusable so hardware keyboards
 * and password managers keep working.
 *
 * Length is not fixed: a credential is whatever the operator set it to. When
 * `length` is given the dots show progress and entry submits on the last digit;
 * without one it accepts any length and waits for an explicit submit.
 */
export function PinPad({
  value,
  onChange,
  onComplete,
  onSubmit,
  length,
  maxLength = 64,
  disabled = false,
  autoFocus = true,
}: {
  value: string;
  onChange: (next: string) => void;
  onComplete?: (value: string) => void;
  onSubmit?: () => void;
  length?: number;
  maxLength?: number;
  disabled?: boolean;
  autoFocus?: boolean;
}) {
  const input = useRef<HTMLInputElement | null>(null);
  const fired = useRef(false);
  const cap = length ?? maxLength;

  useEffect(() => {
    if (autoFocus) input.current?.focus();
  }, [autoFocus]);

  // Auto-submit only makes sense when the length is known in advance.
  useEffect(() => {
    if (length === undefined) return;
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
      if (!key || value.length >= cap) return;
      onChange(value + key);
    },
    [cap, disabled, onChange, value],
  );

  return (
    <div className="flex flex-col items-center gap-6">
      <input
        ref={input}
        id="pin-entry"
        type="tel"
        inputMode="numeric"
        pattern="[0-9]*"
        autoComplete="one-time-code"
        aria-label={length ? `${length}-digit operator PIN` : "Operator PIN"}
        value={value}
        disabled={disabled}
        maxLength={cap}
        onKeyDown={(e) => {
          if (e.key === "Enter" && onSubmit) {
            e.preventDefault();
            onSubmit();
          }
        }}
        onChange={(e) => onChange(e.target.value.replace(/\D/g, "").slice(0, cap))}
        className="absolute h-px w-px opacity-0"
      />

      <button
        type="button"
        onClick={() => input.current?.focus()}
        aria-hidden="true"
        tabIndex={-1}
        className="flex min-h-[1rem] flex-wrap items-center justify-center gap-3"
      >
        {length !== undefined
          ? Array.from({ length }).map((_, i) => (
              <span
                key={i}
                className={`h-3.5 w-3.5 rounded-full border transition-all duration-200 ${
                  i < value.length
                    ? "scale-110 border-brand bg-brand"
                    : "border-hairline bg-transparent"
                }`}
              />
            ))
          : Array.from({ length: value.length }).map((_, i) => (
              <span key={i} className="h-3.5 w-3.5 rounded-full border border-brand bg-brand" />
            ))}
        {length === undefined && value.length === 0 && (
          <span className="text-2xs text-ink-muted">enter your PIN</span>
        )}
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
