/**
 * Opens every page of the built dashboard at a phone and a desktop width against
 * the throwaway API (backend/scripts/site_check_server.py) and fails on a page
 * error, a failed sign-in, or any sideways scroll. Then the phone features: the
 * service worker takes control, a pushed alert becomes a notification, and with
 * the network cut a page turns into the offline page instead of an error.
 *
 *   node scripts/site-check.cjs <trade id>     (dashboard on :3100, API on :8090)
 */
const { chromium } = require("playwright");

const BASE = "http://127.0.0.1:3100";
const API = "http://127.0.0.1:8090";
const tradeId = process.argv[2] || "1";
const PAGES = ["/", "/today", "/algos", "/algos/check", "/trades", `/trades/${tradeId}`, "/reports", "/analytics", "/journal",
  "/replay", "/reliability", "/strategy", "/controls", "/offline"];
const WIDTHS = [[390, 844], [1366, 900]];

(async () => {
  const login = await fetch(`${API}/api/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: "site-check" }) });
  if (!login.ok) { console.error("sign-in to the check API failed:", login.status); process.exit(1); }
  const { token } = await login.json();
  const browser = await chromium.launch(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {});
  let failures = 0;
  for (const [width, height] of WIDTHS) {
    const ctx = await browser.newContext({ viewport: { width, height }, locale: "en-IN", bypassCSP: true });
    await ctx.addCookies([{ name: "meridian_session", value: token, domain: "127.0.0.1", path: "/" }]);
    for (const path of PAGES) {
      const page = await ctx.newPage();
      const errors = [];
      page.on("pageerror", (e) => errors.push(e.message));
      const res = await page.goto(BASE + path, { waitUntil: "domcontentloaded" });
      await page.waitForTimeout(2500);
      const sideways = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      const onLogin = new URL(page.url()).pathname.startsWith("/login");
      const words = (await page.evaluate(() => document.body.innerText)).trim().length;
      const bad = errors.length > 0 || sideways > 1 || onLogin || !res || res.status() >= 400 || words < 40;
      if (bad) failures += 1;
      console.log(`${bad ? "FAIL" : "ok  "} ${width}px ${path}  status ${res ? res.status() : "none"}  page errors ${errors.length}  sideways ${sideways}px${onLogin ? "  (sent to sign-in)" : ""}${errors[0] ? "  " + errors[0] : ""}`);
      await page.close();
    }
    await ctx.close();
  }
  // ── the phone: service worker, a pushed alert, no network ──────────────────
  {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: "en-IN", bypassCSP: true });
    await ctx.addCookies([{ name: "meridian_session", value: token, domain: "127.0.0.1", path: "/" }]);
    const page = await ctx.newPage();
    const check = (ok, what) => { if (!ok) failures += 1; console.log(`${ok ? "ok  " : "FAIL"} phone: ${what}`); };
    await page.goto(`${BASE}/today`, { waitUntil: "domcontentloaded" });
    const controlled = await page.evaluate(async () => {
      const reg = await navigator.serviceWorker.ready;
      for (let i = 0; i < 100 && !navigator.serviceWorker.controller; i += 1) await new Promise((r) => setTimeout(r, 100));
      const kept = await (await caches.open("meridian-static-v1")).keys();
      return { active: !!reg.active, controller: !!navigator.serviceWorker.controller, scope: reg.scope,
        offline: kept.some((k) => new URL(k.url).pathname === "/offline"),
        pages: kept.filter((k) => !/\/_next\/static\/|\/icons\/|\/offline$/.test(new URL(k.url).pathname)).length };
    });
    check(controlled.active && controlled.controller, "the service worker is in control of the page");
    check(controlled.offline, "the offline page is stored on the phone");
    check(controlled.pages === 0, "no page or API answer is stored");
    // A headless browser refuses every notification, so the worker's own handler is given a
    // push and what it asks the phone to show is read back. Delivery by Apple or Google to a
    // real phone is the one step no test here can cover.
    try {
      const worker = ctx.serviceWorkers()[0];
      const shown = await Promise.race([worker.evaluate(async () => {
        const asked = [];
        self.registration.showNotification = async (title, options) => { asked.push({ title, ...options }); };
        for (const data of [{ title: "Trade entered", body: "NIFTY 24500 CE at ₹212.40", kind: "entry", level: "info", ts: 1 },
          { title: "Program crashed", body: "exit code 1", kind: "crash", level: "critical", ts: 2 }]) {
          self.dispatchEvent(new PushEvent("push", { data: JSON.stringify(data) }));
        }
        await new Promise((r) => setTimeout(r, 300));
        return asked;
      }), new Promise((_, reject) => setTimeout(() => reject(new Error("the worker did not answer")), 8000))]);
      check(shown.length === 2 && shown[0].title === "Trade entered" && shown[0].body === "NIFTY 24500 CE at ₹212.40"
        && shown[0].data.url === "/today" && shown[1].requireInteraction === true && shown[1].data.url === "/controls",
      "a pushed alert asks the phone for a notification (a crash stays until tapped and opens Controls)");
    } catch (e) {
      check(false, `a pushed alert asks the phone for a notification (${e.message})`);
    }
    await page.waitForTimeout(11000);                      // the desk saves its figures for the offline page
    await ctx.setOffline(true);
    await page.goto(`${BASE}/controls`, { waitUntil: "domcontentloaded" }).catch(() => undefined);
    await page.waitForTimeout(1500);
    const text = (await page.evaluate(() => document.body.innerText).catch(() => "")).toLowerCase();
    check(text.includes("no connection") && text.includes("old"), "with the network cut, a page becomes the offline page, figures marked old");
    await ctx.close();
  }
  await browser.close();
  console.log(failures ? `${failures} page check(s) failed` : `all ${PAGES.length * WIDTHS.length} page checks and the phone checks passed`);
  process.exit(failures ? 1 : 0);
})();
