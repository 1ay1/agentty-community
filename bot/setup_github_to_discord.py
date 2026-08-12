#!/usr/bin/env python3
"""One-shot: mirror ALL agentty repo activity into Discord.

This wires GitHub's native webhook -> Discord's built-in `/github` adapter, so
every repo event (push, issues, PRs, reviews, releases, stars, forks, branch
and tag create/delete, discussions) is rendered natively in a #github channel
with zero hosting and no per-event workflow code to maintain.

What it does, idempotently:
  1. Creates (or reuses) a #github text channel in the guild.
  2. Creates (or reuses) a `github-activity` webhook on that channel.
  3. Creates (or replaces) a repo webhook on 1ay1/agentty pointing at
     <discord-webhook>/github, subscribed to all the events we care about.

Requirements:
  - bot/.env with DISCORD_BOT_TOKEN + DISCORD_GUILD_ID (Manage Webhooks perm).
  - GitHub CLI (`gh`) authenticated as a repo admin, OR a GITHUB_TOKEN env var
    with `admin:repo_hook` scope.

Run:  .venv/bin/python setup_github_to_discord.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request

REPO = os.environ.get("GITHUB_REPO", "1ay1/agentty")
GUILD_ID = os.environ.get("DISCORD_GUILD_ID", "")
TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
CHANNEL_NAME = os.environ.get("GITHUB_CHANNEL_NAME", "github")
UA = "DiscordBot (https://agentty.org, 1.0)"

# Every event we want mirrored. Discord's /github adapter renders all of these.
EVENTS = [
    "push", "issues", "issue_comment",
    "pull_request", "pull_request_review", "pull_request_review_comment",
    "release", "star", "fork",
    "create", "delete", "discussion", "discussion_comment",
]


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def discord_api(path: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"https://discord.com/api/v10{path}", data=data, method=method,
        headers={
            "Authorization": f"Bot {TOKEN}",
            "User-Agent": UA,               # Cloudflare rejects a missing/blank UA
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def ensure_channel() -> str:
    chans = discord_api(f"/guilds/{GUILD_ID}/channels")
    for c in chans:
        if c.get("name") == CHANNEL_NAME and c.get("type") == 0:
            return c["id"]
    c = discord_api(f"/guilds/{GUILD_ID}/channels", "POST", {
        "name": CHANNEL_NAME, "type": 0,
        "topic": f"Automated repo activity from github.com/{REPO} — "
                 "commits, issues, PRs, releases, stars.",
    })
    return c["id"]


def ensure_webhook(channel_id: str) -> str:
    whs = discord_api(f"/channels/{channel_id}/webhooks")
    for w in whs:
        if w.get("name") == "github-activity":
            return f"https://discord.com/api/webhooks/{w['id']}/{w['token']}"
    w = discord_api(f"/channels/{channel_id}/webhooks", "POST",
                    {"name": "github-activity"})
    return f"https://discord.com/api/webhooks/{w['id']}/{w['token']}"


def gh(*args: str, check: bool = True) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} failed:\n{r.stderr}")
    return r.stdout.strip()


def ensure_repo_hook(discord_url: str) -> None:
    target = discord_url + "/github"
    wh_id = discord_url.rsplit("/", 2)[-2]  # the Discord webhook id
    # Remove any prior hook pointing at the same Discord webhook (idempotent).
    existing = gh("api", f"repos/{REPO}/hooks",
                  "--jq", f'.[] | select(.config.url | contains("{wh_id}")) | .id',
                  check=False)
    for hid in existing.split():
        gh("api", "-X", "DELETE", f"repos/{REPO}/hooks/{hid}", check=False)
        print(f"  removed old repo hook {hid}")
    args = ["api", "-X", "POST", f"repos/{REPO}/hooks",
            "-f", "name=web", "-F", "active=true",
            "-f", f"config[url]={target}", "-f", "config[content_type]=json"]
    for ev in EVENTS:
        args += ["-f", f"events[]={ev}"]
    out = gh(*args, "--jq", "{id, active, events: (.events|length)}")
    print(f"  created repo hook: {out}")


def main() -> None:
    _load_dotenv()
    global GUILD_ID, TOKEN
    GUILD_ID = os.environ.get("DISCORD_GUILD_ID", GUILD_ID)
    TOKEN = os.environ.get("DISCORD_BOT_TOKEN", TOKEN)
    if not TOKEN or not GUILD_ID:
        raise SystemExit("Set DISCORD_BOT_TOKEN and DISCORD_GUILD_ID in .env")

    print(f"→ Ensuring #{CHANNEL_NAME} channel …")
    cid = ensure_channel()
    print(f"  channel id {cid}")
    print("→ Ensuring Discord webhook …")
    url = ensure_webhook(cid)
    print("→ Wiring the GitHub repo webhook …")
    ensure_repo_hook(url)
    print(f"\n✅ Done. All {REPO} activity now posts to #{CHANNEL_NAME}.")


if __name__ == "__main__":
    main()
