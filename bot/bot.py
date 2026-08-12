#!/usr/bin/env python3
"""agentty community bot.

A small, always-on Discord bot for the agentty community server. It:

  • welcomes new members with a friendly DM + a message in the welcome channel
  • answers a handful of slash commands (/docs, /install, /repo, /help)
  • auto-answers common FAQ keywords in the help channel

It is intentionally tiny and dependency-light. The token is read from the
environment (never hard-coded, never committed). Run it anywhere that stays on:
your machine, a Raspberry Pi, Railway, Fly.io, a VPS — anywhere `python bot.py`
can keep running.

Setup:
  1. Create a bot at https://discord.com/developers/applications
       → New Application → Bot → Reset Token → copy it
       → enable the "Server Members Intent" and "Message Content Intent"
  2. Invite it to your server (OAuth2 → URL Generator → scopes: bot,
     applications.commands → permissions: Send Messages, Embed Links,
     Read Message History, Manage Messages optional)
  3. Copy .env.example to .env and fill in the values
  4. pip install -r requirements.txt && python bot.py
"""

from __future__ import annotations

import os
import aiohttp
import discord
from discord import app_commands

# ---------------------------------------------------------------------------
# Config — everything tunable lives here, sourced from the environment.
# ---------------------------------------------------------------------------

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "0") or "0")
WELCOME_CHANNEL_ID = int(os.environ.get("DISCORD_WELCOME_CHANNEL_ID", "0") or "0")
HELP_CHANNEL_ID = int(os.environ.get("DISCORD_HELP_CHANNEL_ID", "0") or "0")

REPO_URL = "https://github.com/1ay1/agentty"
REPO_OWNER, REPO_NAME = "1ay1", "agentty"
DOCS_URL = "https://agentty.org"
INSTALL_URL = "https://agentty.org/docs/installation"
BRAND = 0xC04BFF  # magenta accent for embeds

# FAQ auto-replies: if a help-channel message contains any keyword (case-
# insensitive), the bot replies once with the canned answer. Keep these short.
FAQ = [
    (
        ("install", "download", "how do i get", "set up agentty"),
        f"Install instructions live here: {INSTALL_URL} — prebuilt binaries for "
        "Linux/macOS/Windows, plus Homebrew, AUR, and winget.",
    ),
    (
        ("api key", "auth", "login", "sign in", "oauth", "sk-ant"),
        "On first launch agentty opens auth — paste any provider API key, use a "
        "local Ollama model (no key), or sign in with Claude Pro/Max OAuth.",
    ),
    (
        ("smart mode", "routing", "which model"),
        f"Smart Mode routes each turn to the right model + effort and learns your "
        f"repo over time. Full write-up: {DOCS_URL}/docs/smart-mode",
    ),
    (
        ("rag", "retrieval", "search my code", "index"),
        f"agentty ships a fully local hybrid RAG engine (BM25 + embeddings, "
        f"reranked, GraphRAG-expanded). Details: {DOCS_URL}/docs/retrieval",
    ),
    (
        ("bug", "crash", "segfault", "broken"),
        f"Sorry you hit that! Please open an issue with steps to reproduce: "
        f"{REPO_URL}/issues/new/choose — logs help a lot.",
    ),
]

WELCOME_DM = (
    "Welcome to the **agentty** community! \U0001f44b\n\n"
    "agentty is a fast, native terminal coding agent (C++, single binary, local "
    "RAG, Smart Mode).\n\n"
    f"\u2022 Docs: {DOCS_URL}\n"
    f"\u2022 Install: {INSTALL_URL}\n"
    f"\u2022 Source: {REPO_URL}\n\n"
    "Head to the help channel if you get stuck, and drop a cool session in "
    "showcase. Glad you're here!"
)

# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


@client.event
async def on_ready() -> None:
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    else:
        await tree.sync()
    print(f"agentty-bot online as {client.user} (guilds: {len(client.guilds)})")


@client.event
async def on_member_join(member: discord.Member) -> None:
    # Best-effort DM (may fail if the user blocks DMs) …
    try:
        await member.send(WELCOME_DM)
    except discord.HTTPException:
        pass
    # … plus a public welcome so the server looks alive.
    if WELCOME_CHANNEL_ID:
        chan = member.guild.get_channel(WELCOME_CHANNEL_ID)
        if isinstance(chan, discord.TextChannel):
            await chan.send(
                f"Welcome {member.mention}! Check the docs at {DOCS_URL} and ask "
                "anything in help. \U0001f680"
            )


@client.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return
    # Only act in the designated help channel (0 = any channel).
    if HELP_CHANNEL_ID and message.channel.id != HELP_CHANNEL_ID:
        return

    # Give each question its own thread so #help stays readable.
    if (
        HELP_CHANNEL_ID
        and isinstance(message.channel, discord.TextChannel)
        and message.channel.id == HELP_CHANNEL_ID
        and not isinstance(message.channel, discord.Thread)
        and len(message.content) > 15
    ):
        try:
            title = (message.content[:60] + "…") if len(message.content) > 60 else message.content
            await message.create_thread(name=title, auto_archive_duration=1440)
        except discord.HTTPException:
            pass

    lowered = message.content.lower()
    for keywords, answer in FAQ:
        if any(k in lowered for k in keywords):
            await message.reply(answer, mention_author=False)
            return


@tree.command(name="docs", description="Link to the agentty documentation")
async def docs(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(f"\U0001f4d6 Docs: {DOCS_URL}")


@tree.command(name="install", description="How to install agentty")
async def install(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        f"\u2b07\ufe0f Install: {INSTALL_URL}\nBinaries for Linux/macOS/Windows, "
        "plus Homebrew, AUR, and winget."
    )


@tree.command(name="repo", description="Link to the agentty source repository")
async def repo(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(f"\U0001f4bb Source: {REPO_URL}")


@tree.command(name="help", description="What can this bot do?")
async def help_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "Commands: `/docs` `/install` `/repo` `/release` `/issue`. I also welcome "
        "new members and answer common questions in the help channel. For bugs, "
        f"open an issue: {REPO_URL}/issues/new/choose",
        ephemeral=True,
    )


@tree.command(name="release", description="Show the latest agentty release")
async def release(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    api = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(api, headers={"Accept": "application/vnd.github+json"}) as r:
                data = await r.json()
    except Exception:
        await interaction.followup.send(f"Couldn't reach GitHub. Releases: {REPO_URL}/releases")
        return
    tag = data.get("tag_name", "?")
    notes = (data.get("body") or "")[:1500]
    emb = discord.Embed(
        title=f"🚀 agentty {data.get('name') or tag}",
        url=data.get("html_url", f"{REPO_URL}/releases"),
        description=notes or "See the release page for details.",
        color=BRAND,
    )
    emb.set_footer(text=f"agentty • {tag}")
    await interaction.followup.send(embed=emb)


@tree.command(name="issue", description="Open a prefilled agentty bug/feature issue")
@app_commands.describe(title="Short summary of the bug or request")
async def issue(interaction: discord.Interaction, title: str) -> None:
    from urllib.parse import quote
    url = f"{REPO_URL}/issues/new?title={quote(title)}"
    emb = discord.Embed(
        title="Open a GitHub issue",
        description=f"[Click to file: **{title}**]({url})\n\nAdd steps to reproduce, your OS, and agentty version.",
        color=BRAND,
    )
    await interaction.response.send_message(embed=emb, ephemeral=True)


def main() -> None:
    if not TOKEN:
        raise SystemExit(
            "DISCORD_BOT_TOKEN is not set. Copy .env.example to .env and fill it "
            "in, or export the variable. NEVER commit your token."
        )
    client.run(TOKEN)


if __name__ == "__main__":
    main()
