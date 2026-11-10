/**
 * Polling that only runs while someone can see the page.
 *
 * Every poll is a Vercel function call and a request to the VM that runs the
 * live trader. Measured on 10 Nov 2026, a Deck left open in a background tab
 * made 58 requests a minute, exactly as many as a visible one. A hidden page
 * now makes none; the moment it is shown again it reads once, at once, and the
 * interval resumes.
 *
 * The environment is passed in so this can be tested without a browser.
 */
export interface PollerEnv {
  isVisible: () => boolean;
  /** Subscribe to visibility changes; returns the unsubscribe. */
  onVisibility: (cb: () => void) => () => void;
  setInterval: (cb: () => void, ms: number) => unknown;
  clearInterval: (handle: unknown) => void;
  now: () => number;
}

export interface Poller {
  stop: () => void;
}

export function startVisiblePoller(run: () => void, ms: number, env: PollerEnv, immediate = true): Poller {
  let handle: unknown = null;
  let lastRun = immediate ? env.now() : Number.NEGATIVE_INFINITY;
  let stopped = false;

  const fire = () => {
    lastRun = env.now();
    run();
  };
  const arm = () => {
    if (handle === null) handle = env.setInterval(fire, ms);
  };
  const disarm = () => {
    if (handle !== null) {
      env.clearInterval(handle);
      handle = null;
    }
  };
  const sync = () => {
    if (stopped) return;
    if (env.isVisible()) {
      // Back in view: what is on screen may be as old as the time away.
      if (handle === null && env.now() - lastRun >= ms) fire();
      arm();
    } else {
      disarm();
    }
  };

  if (immediate) run();
  if (env.isVisible()) arm();
  const off = env.onVisibility(sync);
  return {
    stop: () => {
      stopped = true;
      disarm();
      off();
    },
  };
}
