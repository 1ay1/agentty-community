# agentty community

Everything for the [agentty](https://github.com/1ay1/agentty) community: the
Discord bot, a one-shot server provisioner, brand assets, and a release
announcer.

## Layout

- **`brand/`** — logo, chevron icon, animated welcome + icon (GIF/SVG), server
  banner, and the ANSI wordmark. All derived pixel-for-pixel from agentty's
  in-app welcome-screen wordmark. Includes the GIF renderers.
- **`bot/`** — the Discord automation:
  - `setup_server.py` — one-shot REST provisioner (server identity, channels +
    topics, rules/FAQ/intro posts, permanent invite). Idempotent; guild
    auto-detected from the bot's membership.
  - `bot.py` — always-on bot: welcomes members, answers questions via the
    real agentty agent over ACP (`/ask`, @mention, or DM), `/docs` `/install`
    `/repo` `/release` `/issue`, FAQ auto-replies, and #help auto-threading.
  - `setup_github_to_discord.py` — one-shot: mirrors ALL repo activity (push,
    issues, PRs, reviews, releases, stars, forks, branches/tags, discussions)
    into a `#github` channel via GitHub's native webhook → Discord's `/github`
    adapter. Zero hosting, idempotent. Needs `gh` authed as a repo admin.
  - `acp_brain.py` — the bot's brain: drives `agentty acp` over stdio to answer
    questions with the real agent (read-only; all tool permissions auto-denied).
  - `discord-copy.md` — all server text in one place.
  - `README.md` — full setup + run + deploy instructions.
- **`.github/workflows/discord-release.yml`** — posts every published GitHub
  release to Discord via a webhook secret. *(To announce agentty's releases,
  this file also needs to live in the agentty repo — GitHub only fires it on
  the repo it's committed to.)*

## Quick start

```sh
cd bot
cp .env.example .env          # fill DISCORD_BOT_TOKEN (guild auto-detected)
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
.venv/bin/python setup_server.py   # build the server, print the invite
.venv/bin/python setup_github_to_discord.py  # mirror all repo activity to #github
.venv/bin/python bot.py            # run the always-on bot
```

### Automating the repo → Discord

`setup_github_to_discord.py` points GitHub's built-in repository webhook at
Discord's native `/github` endpoint, so **every** repo event renders in a
`#github` channel automatically — no server, no per-event code. Re-run it any
time; it's idempotent (reuses the channel/webhook and replaces the repo hook).
The curated `discord-release.yml` workflow still posts a prettier release embed
to `#announcements`.

See [`bot/README.md`](bot/README.md) for the full walkthrough (creating the bot,
getting the token, deploying).

> The bot token is a secret — it lives in `bot/.env` only, which is gitignored.
> Never commit it.
