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
  - `bot.py` — always-on bot: welcomes members, `/docs` `/install` `/repo`
    `/release` `/issue`, FAQ auto-replies, and #help auto-threading.
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
.venv/bin/python bot.py            # run the always-on bot
```

See [`bot/README.md`](bot/README.md) for the full walkthrough (creating the bot,
getting the token, deploying).

> The bot token is a secret — it lives in `bot/.env` only, which is gitignored.
> Never commit it.
