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
import asyncio
import aiohttp
import discord
from discord import app_commands

from acp_brain import AcpBrain

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

# The bot's brain: the real agentty agent driven over ACP (see acp_brain.py).
# Answers are grounded in the actual agent using the host's own login; the
# session is read-only (all tool permissions are auto-denied).
brain = AcpBrain()


def _chunk(text: str, limit: int = 4000) -> list[str]:
    """Split a long answer into Discord-embed-sized pieces on paragraph/line
    boundaries so we never exceed the 4096-char embed description cap."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        parts.append(text)
    return parts


async def _answer_embeds(question: str) -> list[discord.Embed]:
    """Run the question through the agent and format the reply as embed(s)."""
    try:
        answer = await brain.ask(question)
    except Exception as exc:
        return [discord.Embed(
            title="\u26a0\ufe0f Couldn't reach the agent",
            description=(
                f"Something went wrong asking the agent (`{type(exc).__name__}`). "
                f"Try again in a moment, or browse the docs: {DOCS_URL}/docs"
            ),
            color=BRAND,
        )]
    if not answer:
        return [discord.Embed(
            description=f"I didn't get an answer for that. The docs may help: {DOCS_URL}/docs",
            color=BRAND,
        )]
    pieces = _chunk(answer)
    embeds = [discord.Embed(description=p, color=BRAND) for p in pieces]
    embeds[-1].set_footer(text="agentty \u2022 answered by the agent itself")
    return embeds


@client.event
async def on_ready() -> None:
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    else:
        await tree.sync()
    print(f"agentty-bot online as {client.user} (guilds: {len(client.guilds)})")
    # Pre-warm the ACP agent so the first question isn't slow. Best-effort.
    try:
        await brain._ensure_started()
        print("agentty-bot brain: ACP agent ready")
    except Exception as exc:
        print(f"agentty-bot brain: NOT ready ({exc}) — /ask will retry on demand")


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

    is_dm = message.guild is None
    mentioned = client.user in message.mentions

    # 1. Direct question to the bot: a DM, or an @mention anywhere. Route the
    #    whole thing through the real agent and reply with its answer.
    if is_dm or mentioned:
        # Strip the bot mention out of the text to get the bare question.
        question = message.content
        for m in (f"<@{client.user.id}>", f"<@!{client.user.id}>"):
            question = question.replace(m, "")
        question = question.strip()
        if not question:
            await message.reply(
                "Ask me anything about agentty \U0001f680 e.g. "
                "*how do I enable Smart Mode?* or *how does the local RAG work?*",
                mention_author=False,
            )
            return
        async with message.channel.typing():
            embeds = await _answer_embeds(question)
        await message.reply(embed=embeds[0], mention_author=False)
        for extra in embeds[1:]:
            await message.channel.send(embed=extra)
        return

    # 2. Help channel (0 = any channel): thread long questions, then let the
    #    agent answer. Static FAQ keywords still short-circuit for instant,
    #    zero-cost replies to the most common asks.
    if HELP_CHANNEL_ID and message.channel.id != HELP_CHANNEL_ID:
        return

    # Give each question its own thread so #help stays readable.
    target = message.channel
    if (
        HELP_CHANNEL_ID
        and isinstance(message.channel, discord.TextChannel)
        and message.channel.id == HELP_CHANNEL_ID
        and not isinstance(message.channel, discord.Thread)
        and len(message.content) > 15
    ):
        try:
            title = (message.content[:60] + "\u2026") if len(message.content) > 60 else message.content
            target = await message.create_thread(name=title, auto_archive_duration=1440)
        except discord.HTTPException:
            target = message.channel

    lowered = message.content.lower()
    for keywords, answer in FAQ:
        if any(k in lowered for k in keywords):
            await message.reply(answer, mention_author=False)
            return

    # No canned match in the help channel -> ask the agent, but only for
    # substantial questions (avoid answering "thanks" / one-word chatter).
    if HELP_CHANNEL_ID and len(message.content.strip()) > 15:
        async with target.typing():
            embeds = await _answer_embeds(message.content.strip())
        await target.send(embed=embeds[0])
        for extra in embeds[1:]:
            await target.send(embed=extra)


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


@tree.command(name="ask", description="Ask the agentty agent anything (it answers using the real agent)")
@app_commands.describe(question="Your question about agentty")
async def ask(interaction: discord.Interaction, question: str) -> None:
    await interaction.response.defer(thinking=True)
    embeds = await _answer_embeds(question)
    # First embed as the deferred reply, any overflow as follow-ups.
    await interaction.followup.send(embed=embeds[0])
    for extra in embeds[1:]:
        await interaction.followup.send(embed=extra)


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
