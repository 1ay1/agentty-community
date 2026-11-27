# agentty Discord automation

- **`bot.py`**: the always-on bot (runs here as `agentty-bot.service`).
- **`acp_brain.py`**: drives the real `agentty acp` agent so the bot answers from the actual source.
- **`gh.py`**: a small async GitHub client that uses ETags, so idle polling costs nothing.
- **`setup_server.py`**: one-shot server provisioner (identity, channels, #start-here, invite).
- **`setup_github_to_discord.py`**: creates the webhooks that feed `#github`.

## The server

It's small on purpose: one place where everything happens. Add channels only
once `#campfire` gets too busy to follow.

| Category | Channels |
|---|---|
| 📢 INFO (read-only) | `#start-here`, `#releases` (one card per release, posted by the bot), `#github` (live repo feed via webhooks) |
| 🔥 CAMPFIRE | `#campfire` (the town square), `#help` (each question gets a thread and an instant answer), `#bugs`, 🔊 fireside |
| 🗄 ARCHIVE | old `#feature-requests` / `#off-topic`, read-only |

The bot finds channels **by name**, so keep the names above if you rename things.

## What the bot does

**For everyone**
- **Ask**: @mention, reply, DM, or `/ask`. The answer streams in live, with a
  line showing what the agent is reading. Replying to an answer continues the
  conversation (with memory). Every answer has 👍/👎 buttons.
- **#help**: every new post gets its own thread and a first answer. The bot keeps
  helping the asker in that thread until another human steps in.
- **GitHub**: `#123` in chat unfurls the issue or PR. Right-click a message →
  *Apps → Send to GitHub issue*. Also `/bug`, `/issue`, `/search`.
- **Releases**: polls GitHub. A new release gets a rich card in `#releases` and a
  one-line "it dropped" in `#campfire`. Each crossed multiple of 50 stars gets cheered.
- `/docs` (autocompletes over every docs page), `/install`, `/release`, `/stats`, `/help`.
- Joins get a welcome DM and a warm hello in `#campfire`.

**For the owner (DMs)**
- Every **join** (account age, which invite was used), **leave**, and newcomer's
  **first message**.
- **Every GitHub event** that lands in `#github` (commits, issues, PRs, reviews,
  discussions, stars, forks, tags, releases).
- 👍/👎 feedback on answers, including the question and the answer.
- A **daily pulse** at `PULSE_DAILY_HOUR`. The agent reads the day's chat and
  briefs you on vibe, bugs, requests, confusion, praise, and questions still
  waiting for your reply. Run `/pulse hours:N` any time.
- `/notify` sets what gets DM'd (e.g. GitHub "everything except my own").

State (last release seen, star mark, notify prefs) lives in `state.json`, which is gitignored.

## Setup

1. https://discord.com/developers/applications → New Application → **Bot**. Enable
   **Server Members** and **Message Content** intents. Invite it with scopes
   `bot` + `applications.commands` and the **Administrator** permission.
2. `cp .env.example .env` and fill it in. Never commit `.env`.
3. `python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt`
4. New server? Run `.venv/bin/python setup_server.py` once.
5. Run `.venv/bin/python bot.py`, or use systemd:

```ini
[Service]
User=ayush
WorkingDirectory=/home/ayush/projects/agentty-community/bot
EnvironmentFile=/home/ayush/projects/agentty-community/bot/.env
ExecStart=/home/ayush/projects/agentty-community/bot/.venv/bin/python -u bot.py
Restart=always
```

> ⚠️ The bot token is a full credential. If it leaks, **Reset Token** immediately.
