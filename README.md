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
  - `setup_github_to_discord.py` — one-shot: provisions the per-type activity
    channels + webhooks (`#commits`, `#activity`, `#releases-feed`, `#stars`)
    and prints the `gh secret set` commands. Idempotent. The actual routing
    lives in `agentty`'s `.github/workflows/discord-activity.yml`, which
    forwards each event to the matching channel via Discord's native `/github`
    adapter — so every event still renders natively, just split by type.
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

Repo activity is **split by type** across dedicated channels:

- `#commits` — pushes
- `#activity` — issues, PRs, reviews (+ comments)
- `#releases-feed` — releases, tag/branch create & delete
- `#stars` — stars & forks

Run `setup_github_to_discord.py` to create the channels + webhooks (idempotent),
then run the printed `gh secret set` commands in the agentty repo. The
`discord-activity.yml` workflow there forwards each event's raw payload to the
right channel's Discord `/github` endpoint, so Discord renders every event
natively — just in the right channel. The curated `discord-release.yml` workflow
still posts a prettier release embed to `#announcements`.

See [`bot/README.md`](bot/README.md) for the full walkthrough (creating the bot,
getting the token, deploying).

> The bot token is a secret — it lives in `bot/.env` only, which is gitignored.
> Never commit it.
