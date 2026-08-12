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

Channels:
  #commits        <- push
  #activity       <- issues, PRs, reviews, discussions (+comments)
  #releases-feed  <- release, tag/branch create & delete
  #stars          <- star, fork

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

# channel name -> (topic, GitHub Actions secret name)
CHANNELS = {
    "commits":       ("Pushes & commits from github.com/1ay1/agentty (auto).",
                      "DISCORD_WH_COMMITS"),
    "activity":      ("Issues, PRs, reviews & discussions from 1ay1/agentty (auto).",
                      "DISCORD_WH_ACTIVITY"),
    "releases-feed": ("Published releases & tags from 1ay1/agentty (auto).",
                      "DISCORD_WH_RELEASES"),
    "stars":         ("New stars & forks for 1ay1/agentty (auto).",
                      "DISCORD_WH_STARS"),
}


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


def ensure_webhook(channel_id: str) -> str:
    whs = api(f"/channels/{channel_id}/webhooks")
    wh = next((w for w in whs if w.get("name") == "gh"), None)
    if not wh:
        wh = api(f"/channels/{channel_id}/webhooks", "POST", {"name": "gh"})
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

    print("→ Ensuring channels + webhooks …\n")
    secret_cmds = []
    for name, (topic, secret) in CHANNELS.items():
        cid = ensure_channel(existing, name, topic)
        url = ensure_webhook(cid)
        print(f"  #{name:14} {cid}")
        secret_cmds.append(f"gh secret set {secret} -b '{url}'")

    print("\n✅ Discord side ready. Now wire the routing workflow by running "
          "these in the agentty repo (gh authed as a repo admin):\n")
    print("cd ~/projects/agentty")
    for cmd in secret_cmds:
        print(cmd)
    print("\nThe workflow .github/workflows/discord-activity.yml then routes "
          "each event to the right channel.")


if __name__ == "__main__":
    main()
