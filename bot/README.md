# agentty Discord automation

Two scripts:

- **`setup_server.py`** — one-shot. Configures the whole server (name, icon,
  banner, description, channels + topics, #read-me-first intro, invite).
- **`bot.py`** — always-on. Welcomes new members, answers `/docs` `/install`
  `/repo` `/help`, and auto-replies to FAQ keywords in #help.

All the text lives in **`discord-copy.md`**. Brand images live in `../brand/`.

## 1. Create the bot (2 min, your Discord login)

1. https://discord.com/developers/applications → **New Application** → name it `agentty`.
2. **Bot** → **Reset Token** → copy it. On the same page enable
   **Server Members Intent** and **Message Content Intent**.
3. **OAuth2 → URL Generator** → scopes `bot` + `applications.commands`,
   permission **Administrator** → open the URL → add the bot to your server.
4. Enable **Developer Mode** in Discord (Settings → Advanced), then right-click
   your server icon → **Copy Server ID**.

> ⚠️ The bot token is a full credential. Keep it in `.env` only — never commit
> it, never paste it into a chat. If it leaks, **Reset Token** immediately.

## 2. Configure

```sh
cd community/bot
cp .env.example .env          # then edit .env: DISCORD_BOT_TOKEN + DISCORD_GUILD_ID
```

Install deps. If you have `pip`:

```sh
pip install -r requirements.txt
```

No system `pip` (e.g. Arch/Garuda with an externally-managed Python)? Use a venv:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

…then run everything below with `.venv/bin/python` instead of `python`.

## 3. Provision the server (one shot)

```sh
.venv/bin/python setup_server.py      # or: python setup_server.py
```

It sets identity, creates the channel layout, posts the intro, and prints a
permanent invite link. Safe to re-run (idempotent — existing channels are
reused, not duplicated).

> Banner + description require the server to be **Community-enabled** and/or
> Level-2 boosted. If those fail, the script automatically falls back to setting
> just the name + icon, and tells you.

## 4. Run the always-on bot

```sh
python bot.py
```

Keep it running anywhere that stays on — your machine, a Raspberry Pi, or a free
host (Railway / Fly.io / Render). It needs to stay online to welcome members and
answer questions in real time.

### Deploy on a host (example: systemd on a Linux box)

```ini
# /etc/systemd/system/agentty-bot.service
[Unit]
Description=agentty discord bot
After=network.target
[Service]
WorkingDirectory=/path/to/agentty/community/bot
EnvironmentFile=/path/to/agentty/community/bot/.env
ExecStart=/usr/bin/python3 bot.py
Restart=always
[Install]
WantedBy=multi-user.target
```

```sh
sudo systemctl enable --now agentty-bot
```
