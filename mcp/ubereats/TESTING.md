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

## The tiers are yours, not the author's

A third-party server cannot declare `x-jervis-tier` — that key is Jervis's own
invention — so the guard would block every one of its tools. `config.yaml` supplies
them instead:

| Tools | Tier | Why |
|---|---|---|
| `status`, `search`, `get_restaurant`, `view_cart`, `track_order` | `read` | look, don't touch |
| `login`, `logout`, `set_address`, `add_to_cart`, `clear_cart` | `write` | changes state, spends nothing |
| `checkout` | `confirm` | spends money; Jervis reads it back and waits for a yes |

**Anything not in that list stays blocked.** That is deliberate: `npx -y` fetches the
latest version on every launch, so a tool added in an update must not simply start
working. The version in `config.example.yaml` is pinned (`@0.2.1`) for the same reason
— dropping the pin means running whatever was published most recently.

Config can only *supply* a missing tier, never soften one. A test pins that, so no
config edit can quietly demote `macos.move_to_trash` to `read`.

## Enabling it

```yaml
# ~/.jervis/config.yaml
servers:
  ubereats:
    enabled: true
```

then `launchctl kickstart -k gui/$UID/com.arup.jervis`, and log in once:

> "log in to Uber Eats"

It returns a URL; sign in there and the session persists.

## Checks worth doing, in this order

1. **Browsing only.** "what's near me on Uber Eats" — expect results, no prompts.
2. **A cart, and nothing more.** Add something, then `jervis audit`: you should see
   `write` entries and no `confirm`. Then `clear_cart`.
3. **Decline a checkout.** Ask it to order, and say **no**. Nothing should be charged.
   Check your Uber Eats order history to be sure.
4. **Only then, a real order** — a cheap one, when you actually want food.

## The gap you should know about

PLAN.md §4 Phase 6 specified that `place_order` re-validate the total to within 10% of
the preview, bound to a fresh preview id. **This server does not do that.** Its
`checkout` takes a `confirm` boolean with nothing tying it to the figure you were
shown, and its own README notes that "dynamic pricing and availability may differ".

So Jervis reads back the *instruction*, not the final total. Between your yes and the
charge, the price can move. If that matters to you, the fix is a thin Jervis-side
wrapper that calls `checkout(confirm=false)`, shows you that total, and only then calls
`checkout(confirm=true)` — the drift check the plan asked for. It is not built.
