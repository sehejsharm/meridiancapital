/**
 * Opens every page of the built dashboard at a phone and a desktop width against
 * the throwaway API (backend/scripts/site_check_server.py) and fails on a page
 * error, a failed sign-in, or any sideways scroll.
 *
 *   node scripts/site-check.cjs <trade id>     (dashboard on :3100, API on :8090)
 */
const { chromium } = require("playwright");

const BASE = "http://127.0.0.1:3100";
const API = "http://127.0.0.1:8090";
const tradeId = process.argv[2] || "1";
const PAGES = ["/", "/today", "/algos", "/algos/check", "/trades", `/trades/${tradeId}`, "/reports", "/analytics", "/journal",
  "/replay", "/reliability", "/strategy", "/controls"];
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
  await browser.close();
  console.log(failures ? `${failures} page check(s) failed` : `all ${PAGES.length * WIDTHS.length} page checks passed`);
  process.exit(failures ? 1 : 0);
})();
