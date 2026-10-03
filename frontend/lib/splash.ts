/**
 * Plays the logo animation. The splash lives in the root layout, so it plays
 * by itself on every page load and survives the move from the login page to
 * the desk; this asks it to play again (sign-in, a tap on the logo).
 */
export const SPLASH_EVENT = "meridian:splash";

/** How long the mark is in motion, in milliseconds. */
export const SPLASH_MS = 3000;

export function playSplash(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(SPLASH_EVENT));
}
