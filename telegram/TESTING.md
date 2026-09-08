# Setting up and testing Telegram

Two things only you can do — both involve Telegram accounts, not code.

## 1. Make a bot

In Telegram, message **@BotFather**, send `/newbot`, and follow it. You get a token
that looks like `8123456789:AAH...`. Put it in `~/.jervis/.env`:

```
TELEGRAM_BOT_TOKEN=8123456789:AAH...
```

That token *is* the bot. Anyone holding it can post as it, so it belongs in `.env`
(gitignored) and nowhere else.

## 2. Allow yourself, and only yourself

Message **@userinfobot** to get your numeric Telegram id, then in
`~/.jervis/config.yaml`:

```yaml
telegram:
  allowed_user_ids: [123456789]
```

**This is the whole security model.** The bot is reachable by anyone who learns its
`@handle`, and Jervis runs shell commands, reads files and empties things into the
Trash. An empty allowlist therefore means *nobody* — Jervis refuses to start rather
than answer the world. Do not add anyone you would not hand your unlocked laptop to.

```bash
scripts/doctor.sh          # the `telegram` row should say PASS
scripts/start.sh --telegram
```

## Checks worth doing by hand

1. **It answers you.** Message the bot "what's on my desktop". Expect an answer, with
   a `· macos.list_dir` line showing what it used.
2. **It refuses everyone else.** Easiest test: temporarily remove your id from
   `allowed_user_ids`, restart, and message it. You should be refused, and
   `~/.jervis/audit.jsonl` should gain no entry — the brain never hears it. Put your
   id back afterwards.
3. **Confirmation.** `touch ~/Desktop/test.txt`, then ask it to delete test.txt on the
   desktop. Expect **Yes, go ahead** / **No** buttons. Tap **No** — the file survives.
   Ask again, tap **Yes** — it goes to the Trash and the buttons disappear from the
   message. Typing `yes` instead of tapping works too.
4. **A stale button.** Tap **Yes** on an old confirmation from a previous run. It
   should say the action has already been settled, and nothing should run.
5. **Brain down.** Stop the brain and message it. It should tell you it cannot reach
   its brain, not go silent.
6. **Backlog.** Stop Jervis, send it a message, start it again. It should *not* act on
   what you sent while it was off (`drop_pending_on_start`).

## Things to know

- **Everything you say and everything Jervis answers passes through Telegram's
  servers**, including file contents, message contents and — once Phase 5 lands — bank
  balances. That is inherent to using Telegram, not something this code can fix. If
  that is not acceptable for some of what Jervis can reach, keep those servers
  disabled in `config.yaml` while the bot is running.
- The bot polls (`getUpdates`); it needs no public URL and no port open. It only makes
  outbound connections.
- Each Telegram chat is its own conversation (`telegram:<chat_id>`), separate from the
  REPL's and the voice loop's history.
- The confirm tier, the permission guard and the audit log are unchanged. Telegram is
  only a way in; it gets no privileges the REPL does not have.
