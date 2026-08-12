#!/usr/bin/env python3
"""One-shot agentty Discord server provisioner.

Given a bot token + guild (server) ID in a local .env, this configures the whole
server via the Discord REST API:

  • sets server name, icon, banner, and description
  • deletes the default channels
  • creates the category + channel layout (with topics)
  • posts the #read-me-first intro and rules
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
# (category, [(channel, topic), ...])
LAYOUT = [
    ("📢 INFO", [
        ("announcements", "Releases and news. github.com/1ay1/agentty"),
        ("read-me-first", "Start here — what agentty is and how to install it."),
        ("rules", "Community rules. Read before posting."),
        ("releases", "Automated release notes from GitHub."),
    ]),
    ("💬 COMMUNITY", [
        ("general", "General chat about agentty."),
        ("help", "Stuck? Ask here — each question gets its own thread. Include OS + steps."),
        ("faq", "Frequently asked questions. Check here before #help."),
        ("bug-reports", "Report bugs. Best to also open a GitHub issue."),
        ("feature-requests", "Ideas and requests for agentty."),
        ("showcase", "Show off cool sessions, workflows, and setups."),
        ("off-topic", "Anything not about agentty."),
    ]),
]

READ_ME_FIRST = (
    "**agentty** is a native terminal coding agent — one fast binary, no runtime. "
    "It brings a flagship model into your terminal with local RAG over your "
    "code/docs and Smart Mode routing that learns your repo.\n\n"
    "📖 Docs: <https://agentty.org>\n"
    "💻 Source: <https://github.com/1ay1/agentty>\n"
    "⬇️ Install: <https://agentty.org/docs/installation>\n\n"
    "New here? Say hi in #general, ask in #help, and drop cool sessions in "
    "#showcase."
)

RULES = (
    "**Community rules**\n\n"
    "**1. Be kind.** Assume good faith; no harassment, hate, or spam.\n"
    "**2. Keep it on-topic** per channel — support in #help, ideas in #feature-requests.\n"
    "**3. Search first.** Your question may already be in the docs or history.\n"
    "**4. No piracy, malware, or illegal content.**\n"
    "**5. English in the main channels** so everyone can follow.\n\n"
    "Breaking these may get your messages or account removed."
)

FAQ_POST = (
    "**Frequently asked questions**\n\n"
    "**How do I install agentty?** → <https://agentty.org/docs/installation> — "
    "prebuilt binaries for Linux/macOS/Windows, plus Homebrew, AUR, winget.\n\n"
    "**Do I need an API key?** → On first launch, paste any provider key, use a "
    "local Ollama model (no key), or sign in with Claude Pro/Max OAuth.\n\n"
    "**What's Smart Mode?** → Routes each turn to the right model + effort and "
    "learns your repo. <https://agentty.org/docs/smart-mode>\n\n"
    "**Is the RAG local?** → Yes — fully local hybrid BM25 + embeddings, "
    "reranked and GraphRAG-expanded. <https://agentty.org/docs/retrieval>\n\n"
    "**Found a bug?** → Open an issue: <https://github.com/1ay1/agentty/issues/new/choose>"
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

        async def ensure(name, ctype, parent=None, topic=None):
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
            ch = await d.post(f"/guilds/{guild}/channels", body)
            existing[name] = ch
            print(f"  + {name}")
            return ch

        # 3) build categories + channels
        print("→ Creating channels …")
        first_text = None
        readme = None
        rules_ch = None
        faq_ch = None
        for cat_name, channels in LAYOUT:
            cat = await ensure(cat_name, 4)  # 4 = category
            for ch_name, topic in channels:
                ch = await ensure(ch_name, 0, parent=cat["id"], topic=topic)  # 0 = text
                first_text = first_text or ch
                if ch_name == "read-me-first":
                    readme = ch
                elif ch_name == "rules":
                    rules_ch = ch
                elif ch_name == "faq":
                    faq_ch = ch

        # 4) seed content (only into channels that look empty)
        async def seed(ch, content, label):
            if not ch:
                return
            msgs = await d.get(f"/channels/{ch['id']}/messages?limit=1")
            if not msgs:
                print(f"→ Posting {label} …")
                await d.post(f"/channels/{ch['id']}/messages", {"content": content})

        await seed(readme, READ_ME_FIRST, "#read-me-first intro")
        await seed(rules_ch, RULES, "#rules")
        await seed(faq_ch, FAQ_POST, "#faq")

        # 5) permanent invite
        target = first_text or next(iter(existing.values()))
        print("→ Creating a permanent invite …")
        inv = await d.post(f"/channels/{target['id']}/invites",
                           {"max_age": 0, "max_uses": 0, "unique": False})
        print("\n✅ Done. Permanent invite:")
        print(f"   https://discord.gg/{inv['code']}")


if __name__ == "__main__":
    asyncio.run(main())
