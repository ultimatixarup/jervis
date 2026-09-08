/**
 * Sign in to Uber Eats in a visible browser, and keep the cookies.
 *
 * The upstream server's `ubereats_login` only returns a URL: it drives a headless
 * browser and has no way to receive a session established anywhere else, so signing in
 * elsewhere never reaches it. This opens the browser it actually uses, lets Arup sign
 * in there himself, and writes the resulting cookies where that server reads them.
 *
 * patchright rather than plain Playwright on purpose - same stealth patches and the
 * same browser build the upstream server uses. A vanilla Playwright browser is what
 * Uber Eats' bot detection is looking for.
 *
 * Nothing here reads, types or stores a password. The browser is a real window; the
 * only thing this process keeps is the session cookie, in the file that server owns.
 */
const fs = require("fs");
const os = require("os");
const path = require("path");
const { chromium } = require("patchright");

const COOKIE_DIR = path.join(os.homedir(), ".strider", "ubereats");
const COOKIE_FILE = path.join(COOKIE_DIR, "cookies.json");
const LOGIN_URL = "https://www.ubereats.com/login";

// What the upstream server counts as a session (its auth.ts getAuthState).
const SESSION_COOKIES = new Set(["uev2.id", "sid", "uev2.tok", "jwt-session"]);

const timeoutSeconds = Number(process.argv[2] || 300);

function report(payload) {
  process.stdout.write(JSON.stringify(payload) + "\n");
}

async function main() {
  const browser = await chromium.launch({
    headless: false, // the entire point
    args: ["--disable-blink-features=AutomationControlled"],
  });
  const context = await browser.newContext();

  // Carry over an existing session so a re-run tops it up rather than starting over.
  try {
    const existing = JSON.parse(fs.readFileSync(COOKIE_FILE, "utf-8"));
    if (Array.isArray(existing) && existing.length) await context.addCookies(existing);
  } catch {
    /* first run */
  }

  const page = await context.newPage();
  await page.goto(LOGIN_URL, { waitUntil: "domcontentloaded" }).catch(() => {});

  const deadline = Date.now() + timeoutSeconds * 1000;
  let signedIn = false;

  while (Date.now() < deadline) {
    if (browser.isConnected() === false) break;
    let cookies = [];
    try {
      cookies = await context.cookies("https://www.ubereats.com");
    } catch {
      break; // the window was closed
    }
    if (cookies.some((c) => SESSION_COOKIES.has(c.name))) {
      signedIn = true;
      break;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }

  if (signedIn) {
    const all = await context.cookies();
    fs.mkdirSync(COOKIE_DIR, { recursive: true });
    // The session is a bearer token for the account. Owner-only.
    fs.writeFileSync(COOKIE_FILE, JSON.stringify(all, null, 2), { mode: 0o600 });
    fs.chmodSync(COOKIE_FILE, 0o600);
  }

  await browser.close().catch(() => {});
  report(
    signedIn
      ? { success: true, cookies: COOKIE_FILE }
      : { success: false, error: `no sign-in within ${timeoutSeconds}s` }
  );
  process.exit(signedIn ? 0 : 1);
}

main().catch((error) => {
  report({ success: false, error: String((error && error.message) || error) });
  process.exit(1);
});
