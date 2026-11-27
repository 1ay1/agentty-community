# agentty community

Everything for the [agentty](https://github.com/agentty-org/agentty) community:
the Discord bot, the server provisioner, and brand assets.

## Layout

- **`brand/`**: logo, chevron icon, animated welcome and icon (GIF/SVG), server
  banner, and the ANSI wordmark. All of it is derived pixel-for-pixel from
  agentty's in-app welcome-screen wordmark. The GIF renderers are included.
- **`bot/`**: the Discord side. See [`bot/README.md`](bot/README.md).
  - `bot.py` is the always-on bot. It answers through the real agentty agent,
    threads #help questions, unfurls `#123`, files issues, posts release cards,
    and DMs the owner about joins, GitHub events, and a daily pulse.
  - `acp_brain.py` drives `agentty acp` read-only over stdio.
  - `gh.py` is a small ETag-cached GitHub client.
  - `setup_server.py` is the one-shot provisioner for the small layout:
    INFO (`#start-here` `#releases` `#github`) + CAMPFIRE (`#campfire` `#help` `#bugs`).
  - `setup_github_to_discord.py` creates the `#github` webhooks and prints the
    `gh secret set` commands. The routing lives in agentty's
    `.github/workflows/discord-activity.yml`, which forwards every event through
    Discord's native `/github` adapter.

## Quick start

```sh
cd bot
cp .env.example .env               # fill DISCORD_BOT_TOKEN + DISCORD_GUILD_ID
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
.venv/bin/python setup_server.py   # build the server, print the invite
.venv/bin/python setup_github_to_discord.py  # wire repo activity into #github
.venv/bin/python bot.py            # run the always-on bot
```

> The bot token is a secret. It lives only in `bot/.env`, which is gitignored.
> Never commit it.
