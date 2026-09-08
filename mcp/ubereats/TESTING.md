# Uber Eats — read this before enabling it

Jervis uses the third-party server **`@striderlabs/mcp-ubereats`** rather than the
hand-written Playwright client PLAN.md §4 Phase 6 sketched. It does the same job with
the same technique, and it is already written.

**It spends real money on the card saved to your Uber Eats account, and Jervis is
reachable from Telegram.** That combination is why everything below exists.

## What you are trusting

- **Three published versions**, MIT, last touched June 2026. It is a small project,
  not an audited one.
- It depends on **`patchright`**, a Playwright fork built to evade bot detection. That
  tells you Uber Eats actively blocks automation: expect it to break when the site
  changes, and assume it is against their terms of service.
- Ordering runs through a browser session it stores at
  `~/.strider/ubereats/cookies.json`. Anyone with that file is logged in as you.

## The tiers

Jervis runs the upstream server as a **child process** and re-exposes its tools with
tiers of its own, so there is one browser session and one place ordering can happen:

| Tools | Tier |
|---|---|
| `status`, `search`, `get_restaurant`, `view_cart`, `track_order`, `preview_order` | `read` |
| `login`, `logout`, `set_address`, `add_to_cart`, `clear_cart` | `write` |
| `place_order` | `confirm` — Jervis reads it back and waits |

The upstream version is pinned (`@0.2.1` in `child.py`), because `npx -y` otherwise
fetches whatever was published most recently.

## Enabling it

Two one-time steps:

```bash
npx patchright install chromium          # ~150MB, the browser it drives
cd mcp/ubereats/node && npm install      # the sign-in helper
```

Then:

```yaml
# ~/.jervis/config.yaml
servers:
  ubereats: { enabled: true }
```

then `launchctl kickstart -k gui/$UID/com.arup.jervis`, and sign in once:

> "sign in to Uber Eats"

**A real browser window opens.** Sign in there yourself — Jervis never sees the
password, and nothing in the helper can type into a field (there is a test asserting
it uses no input API at all). When it detects a session it saves the cookies, closes
the window, and the next `status` picks them up.

### Why Jervis does its own sign-in

The upstream `ubereats_login` cannot work. It returns the string
`https://www.ubereats.com/login` and nothing else, while the server drives a *headless*
browser with `headless: true` hardcoded and no way to override it. Signing in anywhere
else never reaches that browser: the two share nothing. Its cookie file stays an empty
array and `status` keeps saying you are signed out, however many times you log in.

So `mcp/ubereats` runs its own helper (`node/login.js`) using **patchright** — the same
stealth browser build the upstream server uses, because a vanilla Playwright browser is
what Uber Eats' bot detection is looking for. It waits for the cookies that server
actually recognises (`uev2.id`, `sid`, `uev2.tok`, `jwt-session`) and writes them, mode
`0600`, to the path it reads.

That file is a bearer token for your account. Treat it like a password:

```bash
ls -l ~/.strider/ubereats/cookies.json     # should be -rw-------
```

## Checks worth doing, in this order

1. **Browsing only.** "what's near me on Uber Eats" — expect results, no prompts.
2. **A cart, and nothing more.** Add something, then `jervis audit`: you should see
   `write` entries and no `confirm`. Then `clear_cart`.
3. **Decline a checkout.** Ask it to order, and say **no**. Nothing should be charged.
   Check your Uber Eats order history to be sure.
4. **Only then, a real order** — a cheap one, when you actually want food.

## How the price check works

The upstream `checkout` takes only a `confirm` boolean, with nothing tying it to the
figure you were shown — and its own README says "dynamic pricing and availability may
differ". So Jervis does not expose it. Ordering goes:

1. **`preview_order`** (`read`) prices the cart, returns the total and a preview id.
   Nothing is charged.
2. **`place_order(preview_id)`** (`confirm`) re-prices immediately, compares against the
   total you were shown, and places the order **only** if it is within 10%.

A preview is single-use, expires after five minutes, and is discarded when the cart is
cleared or when a placement is refused — so a rising price cannot be retried until it
slips through. If the total cannot be read out of the preview at all, nothing is
placed: approving one figure and being charged another is the failure this exists to
prevent.

There is no other route. A test asserts no tool named `checkout` is exposed, and that
`confirm=True` appears exactly once in the source, inside `place_order` — in the spirit
of the money-movement grep in PLAN.md §4 Phase 5.
