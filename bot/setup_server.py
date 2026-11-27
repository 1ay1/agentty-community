#!/usr/bin/env python3
"""One-shot agentty Discord server provisioner.

Given a bot token + guild (server) ID in a local .env, this configures the whole
server via the Discord REST API:

  • sets server name, icon, banner, and description
  • creates the small category + channel layout (with topics); INFO is read-only
  • posts the #start-here intro
  • creates a permanent invite and prints it

Run it ONCE. It's idempotent: channels that already exist (matched by name) are
reused/updated, not duplicated, so re-running is safe.

Setup:
  1. Create a bot + add it to your server (Administrator perm) — see bot.py header.
  2. Copy .env.example to .env and fill DISCORD_BOT_TOKEN + DISCORD_GUILD_ID.
  3. pip install -r requirements.txt
  4. python setup_server.py

The token is read from the environment / .env only. NEVER commit it.
"""

from __future__ import annotations

import asyncio
import base64
import os
import sys
from pathlib import Path

import aiohttp


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (no dependency): KEY=VALUE lines into os.environ."""
    p = Path(__file__).resolve().parent / path
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

API = "https://discord.com/api/v10"
BRAND = Path(__file__).resolve().parent.parent / "brand"

# ── server identity ─────────────────────────────────────────────────────────
SERVER_NAME = "agentty"
SERVER_DESCRIPTION = (
    "The home of agentty — a fast, native terminal coding agent with local RAG "
    "and Smart Mode. Help, ideas, and showcase."
)
ICON_FILE = BRAND / "agentty-icon.png"
BANNER_FILE = BRAND / "agentty-server-banner.png"

# ── channel layout ──────────────────────────────────────────────────────────
# (category, [(channel, topic), ...]). Kept small on purpose: one place where
# everything happens (#campfire). Add channels only when it gets too busy.
LAYOUT = [
    ("📢 INFO", [
        ("start-here", "What agentty is, where things live, and how to ask the bot."),
        ("releases", "Every agentty release, the moment it drops. 🚀"),
        ("github", "Live feed of agentty-org/agentty — commits, issues, PRs, reviews, stars & forks."),
    ]),
    ("🔥 CAMPFIRE", [
        ("campfire", "The town square — chat, ideas, feedback, hot takes, show-and-tell. When in doubt, post here. 🔥"),
        ("help", "Stuck? Ask here — include your OS, agentty version, and what you ran."),
        ("bugs", "Something broke? Post it here. Right-click a message → Apps → Send to GitHub issue."),
    ]),
]

# Everyone can read INFO; only the bot/webhooks post there.
INFO_CATEGORY = "📢 INFO"
DENY_POST = (1 << 11) | (1 << 35) | (1 << 36) | (1 << 38)   # send, threads, send-in-threads

START_HERE = (
    "## 🔥 welcome to the agentty campfire\n\n"
    "**agentty** is a native terminal coding agent. It ships as one fast C++ "
    "binary, has local RAG over your code and docs, and Smart Mode picks the "
    "model and effort for each turn. A small, opinionated group hangs out here.\n\n"
    "**Where things are**\n"
    "{campfire} — **everything happens here.** Chat, ideas, feedback, hot takes, show-and-tell.\n"
    "{help} — stuck? Post your question and you'll get a thread with an answer right away.\n"
    "{bugs} — something broke? Include `agentty --version`, your OS, and your terminal.\n"
    "{releases} — a ping each time a new version drops.\n"
    "{github} — live feed of the repo: commits, issues, PRs, stars.\n\n"
    "**The bot is agentty itself**, running against its own source. It reads the "
    "code before it answers.\n"
    "• @mention it, reply to it, DM it, or use `/ask`. Reply to its answer to keep going.\n"
    "• Write `#123` in chat to show that issue or PR.\n"
    "• Right-click any message → **Apps → Send to GitHub issue**.\n"
    "• `/install` `/docs` `/release` `/stats` `/search` `/bug`\n\n"
    "**One rule:** be kind. Assume good faith. That's it.\n\n"
    "📖 <https://agentty.org/docs/> · 💻 <https://github.com/agentty-org/agentty> · "
    "⬇️ `curl -fsSL https://agentty.org/install.sh | sh`"
)


def _b64_image(path: Path) -> str | None:
    if not path.exists():
        print(f"  (skip) image not found: {path.name}")
        return None
    data = base64.b64encode(path.read_bytes()).decode()
    return f"data:image/png;base64,{data}"


class Discord:
    def __init__(self, session: aiohttp.ClientSession, token: str):
        self.s = session
        self.h = {"Authorization": f"Bot {token}"}

    async def _req(self, method: str, path: str, **kw):
        async with self.s.request(method, API + path, headers=self.h, **kw) as r:
            if r.status == 429:  # rate limited — respect retry_after and retry
                retry = (await r.json()).get("retry_after", 1)
                await asyncio.sleep(retry + 0.5)
                return await self._req(method, path, **kw)
            if r.status >= 400:
                raise RuntimeError(f"{method} {path} -> {r.status}: {await r.text()}")
            return await r.json() if r.content_type == "application/json" else None

    get = lambda self, p: self._req("GET", p)
    post = lambda self, p, j: self._req("POST", p, json=j)
    patch = lambda self, p, j: self._req("PATCH", p, json=j)
    delete = lambda self, p: self._req("DELETE", p)


async def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    guild = os.environ.get("DISCORD_GUILD_ID", "")
    if not token:
        sys.exit("Set DISCORD_BOT_TOKEN (see .env.example). Guild is auto-detected.")

    async with aiohttp.ClientSession() as session:
        d = Discord(session, token)

        # Verify the token and find which guild(s) the bot is in. If the
        # configured guild id is empty or the bot isn't in it, fall back to the
        # bot's sole guild (the common case right after inviting it).
        me = await d.get("/users/@me")
        guilds = await d.get("/users/@me/guilds")
        guild_ids = {g["id"] for g in guilds}
        if not guilds:
            sys.exit(
                f"Bot '{me.get('username')}' is in 0 servers. Invite it first:\n"
                f"  https://discord.com/api/oauth2/authorize?client_id={me['id']}"
                f"&permissions=8&scope=bot%20applications.commands"
            )
        if guild not in guild_ids:
            if len(guilds) == 1:
                guild = guilds[0]["id"]
                print(f"→ Using the bot's server: {guilds[0]['name']} ({guild})")
            else:
                sys.exit(
                    "DISCORD_GUILD_ID isn't one of the bot's servers. It's in:\n"
                    + "\n".join(f"  {g['id']}  {g['name']}" for g in guilds)
                )

        # 1) identity: name, description, icon, banner
        print("→ Setting server identity …")
        payload: dict = {"name": SERVER_NAME, "description": SERVER_DESCRIPTION}
        icon = _b64_image(ICON_FILE)
        if icon:
            payload["icon"] = icon
        banner = _b64_image(BANNER_FILE)
        if banner:
            payload["banner"] = banner
        try:
            await d.patch(f"/guilds/{guild}", payload)
        except RuntimeError as e:
            # banner/description need boost level / COMMUNITY; retry without them
            print(f"  full identity failed ({e}); retrying name+icon only")
            await d.patch(f"/guilds/{guild}", {"name": SERVER_NAME, **({"icon": icon} if icon else {})})

        # 2) fetch existing channels so we're idempotent
        existing = {c["name"]: c for c in await d.get(f"/guilds/{guild}/channels")}

        async def ensure(name, ctype, parent=None, topic=None, locked=False):
            lock = [{"id": guild, "type": 0, "allow": "0", "deny": str(DENY_POST)}]
            if name in existing:
                ch = existing[name]
                if topic and ch.get("topic") != topic and ctype == 0:
                    await d.patch(f"/channels/{ch['id']}", {"topic": topic})
                return ch
            body = {"name": name, "type": ctype}
            if parent:
                body["parent_id"] = parent
            if topic:
                body["topic"] = topic
            if locked:
                body["permission_overwrites"] = lock
            ch = await d.post(f"/guilds/{guild}/channels", body)
            existing[name] = ch
            print(f"  + {name}")
            return ch

        # 3) build categories + channels
        print("→ Creating channels …")
        made: dict[str, dict] = {}
        for cat_name, channels in LAYOUT:
            locked = cat_name == INFO_CATEGORY
            cat = await ensure(cat_name, 4, locked=locked)  # 4 = category
            for ch_name, topic in channels:
                made[ch_name] = await ensure(ch_name, 0, parent=cat["id"], topic=topic,
                                             locked=locked)  # 0 = text

        # 4) seed content (only into channels that look empty)
        async def seed(ch, content, label):
            if not ch:
                return
            msgs = await d.get(f"/channels/{ch['id']}/messages?limit=1")
            if not msgs:
                print(f"→ Posting {label} …")
                await d.post(f"/channels/{ch['id']}/messages", {"content": content})

        await seed(made.get("start-here"),
                   START_HERE.format(**{k: f"<#{v['id']}>" for k, v in made.items()
                                        if k in ("campfire", "help", "bugs", "releases", "github")}),
                   "#start-here")

        # 5) permanent invite
        target = made.get("start-here") or next(iter(existing.values()))
        print("→ Creating a permanent invite …")
        inv = await d.post(f"/channels/{target['id']}/invites",
                           {"max_age": 0, "max_uses": 0, "unique": False})
        print("\n✅ Done. Permanent invite:")
        print(f"   https://discord.gg/{inv['code']}")


if __name__ == "__main__":
    asyncio.run(main())
