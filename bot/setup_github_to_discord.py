#!/usr/bin/env python3
"""One-shot: create the per-type Discord channels + webhooks for repo activity,
and print the webhook URLs to store as GitHub Actions secrets.

Repo activity is SPLIT across channels by event type. Because Discord's native
`/github` adapter is one-channel-per-webhook, the routing itself lives in a
GitHub Actions workflow (.github/workflows/discord-activity.yml in the agentty
repo) that forwards each event's raw payload to the matching channel webhook —
so Discord still renders every event natively, just in the right channel.

This script only provisions the Discord side (idempotent): channels + webhooks.
It then prints `gh secret set …` commands to wire the workflow. Run those with
`gh` authed as an agentty-repo admin.

Channel: #github (public, read-only) gets four webhooks, one per secret:
  DISCORD_WH_COMMITS   <- push
  DISCORD_WH_ACTIVITY  <- issues, PRs, reviews, discussions (+comments)
  DISCORD_WH_RELEASES  <- release, tag/branch create & delete
  DISCORD_WH_STARS     <- star, fork
(#releases gets one clean card per release from the bot, not from webhooks.)

Requirements: bot/.env with DISCORD_BOT_TOKEN + DISCORD_GUILD_ID (Manage
Webhooks + Manage Channels perms).

Run:  .venv/bin/python setup_github_to_discord.py
"""

from __future__ import annotations

import json
import os
import urllib.request

GUILD_ID = os.environ.get("DISCORD_GUILD_ID", "")
TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
UA = "DiscordBot (https://agentty.org, 1.0)"

# All four GitHub Actions secrets point at webhooks in ONE public #github
# channel (the workflow still routes by type, so splitting again later is just
# a matter of moving a webhook). channel name -> topic, [secret names]
GITHUB_CHANNEL = "github"
GITHUB_TOPIC = "Live feed of agentty-org/agentty — commits, issues, PRs, reviews, stars & forks."
SECRETS = ["DISCORD_WH_COMMITS", "DISCORD_WH_ACTIVITY", "DISCORD_WH_RELEASES", "DISCORD_WH_STARS"]


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def api(path: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"https://discord.com/api/v10{path}", data=data, method=method,
        headers={
            "Authorization": f"Bot {TOKEN}",
            "User-Agent": UA,          # Cloudflare rejects a missing/blank UA
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def ensure_channel(existing: dict, name: str, topic: str) -> str:
    ch = existing.get(name)
    if ch:
        return ch["id"]
    ch = api(f"/guilds/{GUILD_ID}/channels", "POST",
             {"name": name, "type": 0, "topic": topic})
    return ch["id"]


def ensure_webhook(channel_id: str, name: str) -> str:
    whs = api(f"/channels/{channel_id}/webhooks")
    wh = next((w for w in whs if w.get("name") == name), None)
    if not wh:
        wh = api(f"/channels/{channel_id}/webhooks", "POST", {"name": name})
    return f"https://discord.com/api/webhooks/{wh['id']}/{wh['token']}"


def main() -> None:
    _load_dotenv()
    global GUILD_ID, TOKEN
    GUILD_ID = os.environ.get("DISCORD_GUILD_ID", GUILD_ID)
    TOKEN = os.environ.get("DISCORD_BOT_TOKEN", TOKEN)
    if not TOKEN or not GUILD_ID:
        raise SystemExit("Set DISCORD_BOT_TOKEN and DISCORD_GUILD_ID in .env")

    existing = {c["name"]: c for c in api(f"/guilds/{GUILD_ID}/channels")
                if c.get("type") == 0}

    print("→ Ensuring #github + webhooks …\n")
    cid = ensure_channel(existing, GITHUB_CHANNEL, GITHUB_TOPIC)
    secret_cmds = []
    for secret in SECRETS:
        url = ensure_webhook(cid, "gh-" + secret.removeprefix("DISCORD_WH_").lower())
        print(f"  #{GITHUB_CHANNEL} {cid}  webhook {secret}")
        secret_cmds.append(f"gh secret set {secret} -R agentty-org/agentty -b '{url}'")

    print("\n✅ Discord side ready. Now wire the routing workflow by running "
          "these in the agentty repo (gh authed as a repo admin):\n")
    print("cd ~/projects/agentty")
    for cmd in secret_cmds:
        print(cmd)
    print("\nThe workflow .github/workflows/discord-activity.yml then forwards "
          "every event to #github.")


if __name__ == "__main__":
    main()
