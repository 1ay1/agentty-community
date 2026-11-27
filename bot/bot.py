#!/usr/bin/env python3
"""agentty community bot.

One always-on bot for a small, cozy server. Channels are resolved by NAME at
runtime (#start-here #releases #github #campfire #help #bugs), so renaming the
token/env never has to change when the layout does.

  Ask      @mention / reply / DM / /ask → the real agentty agent (over ACP) reads
           the actual source and answers, streamed live with its tool activity.
           Replies keep conversational memory; 👍/👎 on every answer.
  #help    every new question gets a thread and an instant first answer.
  GitHub   `#123` in chat unfurls the issue/PR; right-click a message →
           Apps → "Send to GitHub issue"; /issue /search /stats /release.
  Releases polls GitHub; a new release gets a rich post in #releases and a
           one-line "it dropped" in #campfire. Star milestones get cheered.
  Owner    the server owner is DM'd about every join (invite used, account
           age), leave, newcomer's first message, every GitHub event that lands
           in #github/#releases, and answer feedback. /pulse summarises what
           the community is saying; a daily pulse DM lands in the evening.

Outside #help it stays silent unless addressed. Secrets come from .env only.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import discord
from discord import app_commands

from acp_brain import AcpBrain, ACP_WORKSPACE
from gh import GitHub, REPO

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "0") or "0")
OWNER_ID_ENV = int(os.environ.get("DISCORD_OWNER_ID", "0") or "0")
OWNER_GH = os.environ.get("OWNER_GITHUB_LOGIN", "1ay1")
PULSE_HOUR = os.environ.get("PULSE_DAILY_HOUR", "21")        # local hour; "" = off
RELEASE_POLL = float(os.environ.get("RELEASE_POLL_SECONDS", "120"))
STAR_STEP = int(os.environ.get("STAR_MILESTONE_STEP", "50"))

REPO_URL = f"https://github.com/{REPO}"
SITE = "https://agentty.org"
DOCS_URL = f"{SITE}/docs/"
INSTALL_URL = f"{SITE}/docs/installation/"
BRAND = 0xC04BFF
STATE_PATH = Path(__file__).with_name("state.json")

CH_START, CH_RELEASES, CH_GITHUB = "start-here", "releases", "github"
CH_CAMPFIRE, CH_HELP, CH_BUGS = "campfire", "help", "bugs"
PULSE_CHANNELS = (CH_CAMPFIRE, CH_HELP, CH_BUGS)

INSTALL = {
    "linux": ("Linux", "curl -fsSL https://agentty.org/install.sh | sh"),
    "macos": ("macOS", "brew tap 1ay1/tap && brew install agentty"),
    "windows": ("Windows", "winget install agentty\n# or: irm https://agentty.org/install.ps1 | iex"),
    "arch": ("Arch", "yay -S agentty-bin"),
}

WELCOME_DM = (
    "Welcome to the **agentty** campfire \U0001f525\n\n"
    "It's a small room of people who care a lot about their tools — say hi in "
    "**#campfire**, that's where everything happens.\n\n"
    "\u2022 Ask me anything: @mention me, reply to me, or DM me here. I read the "
    "real agentty source before answering.\n"
    f"\u2022 Docs: <{DOCS_URL}>  \u2022 Install: <{INSTALL_URL}>  \u2022 Source: <{REPO_URL}>"
)

CAMPFIRE_WELCOMES = [
    "\U0001f525 {m} pulled up a log by the fire. Welcome!",
    "\U0001f43a {m} joined the pack. Say hi!",
    "\U0001f319 {m} wandered in from the dark. Welcome to the campfire.",
    "\U0001fab5 Make room — {m} just sat down. Welcome!",
    "\u2728 {m} is here. Welcome to agentty's corner of the internet.",
]
WELCOME_TAIL = ("\n-# Tell us what you're building. Questions about agentty? "
                "@mention me — I read the real source.")


# ---------------------------------------------------------------------------
# Persistent state (gitignored JSON next to the bot)
# ---------------------------------------------------------------------------

class State:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.d = json.loads(path.read_text())
        except Exception:
            self.d = {}
        self.d.setdefault("notify_github", "all")      # all | others | off
        self.d.setdefault("notify_joins", True)
        self.d.setdefault("fresh", [])                 # joined, not yet posted

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v) -> None:
        self.d[k] = v
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, indent=1))
        tmp.replace(self.path)


state = State(STATE_PATH)
brain = AcpBrain()
github = GitHub()


# ---------------------------------------------------------------------------
# Discord markdown helpers
# ---------------------------------------------------------------------------

_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")


def _split_row(line: str) -> list[str]:
    line = line.strip().strip("|")
    cells = re.split(r"(?<!\\)\|", line)
    return [c.replace("\\|", "|").strip() for c in cells]


def _strip_inline_md(s: str) -> str:
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", s)
    s = re.sub(r"`(.+?)`", r"\1", s)
    s = re.sub(r"~~(.+?)~~", r"\1", s)
    s = re.sub(r"\[(.+?)\]\((.+?)\)", r"\1", s)
    return s


def _render_table(rows: list[str]) -> str:
    grid = []
    for r in rows:
        if _TABLE_SEP.match(r) and "-" in r:
            continue
        grid.append([_strip_inline_md(c) for c in _split_row(r)])
    if not grid:
        return ""
    ncol = max(len(r) for r in grid)
    for r in grid:
        r += [""] * (ncol - len(r))
    widths = [max(len(r[c]) for r in grid) for c in range(ncol)]
    out = []
    for i, r in enumerate(grid):
        out.append("  ".join(r[c].ljust(widths[c]) for c in range(ncol)).rstrip())
        if i == 0:
            out.append("  ".join("-" * widths[c] for c in range(ncol)))
    return "```\n" + "\n".join(out) + "\n```"


def md_for_discord(text: str) -> str:
    """Discord can't render GFM tables — turn them into aligned code blocks."""
    lines = (text or "").split("\n")
    out: list[str] = []
    i = 0
    in_fence = False
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if not in_fence and _TABLE_ROW.match(line):
            block = []
            while i < len(lines) and _TABLE_ROW.match(lines[i]):
                block.append(lines[i])
                i += 1
            if len(block) >= 2 and any(_TABLE_SEP.match(b) and "-" in b for b in block):
                out.append(_render_table(block))
            else:
                out.extend(block)
            continue
        out.append(line)
        i += 1
    return "\n".join(out).strip()


def chunk(text: str, limit: int = 2000) -> list[str]:
    """Split into message-sized pieces without ever breaking a code fence."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    lines = text.split("\n")
    parts: list[str] = []
    buf: list[str] = []
    in_fence = False

    def buflen(extra: str) -> int:
        return sum(len(x) + 1 for x in buf) + len(extra)

    def flush() -> None:
        nonlocal buf
        piece = "\n".join(buf).strip()
        if in_fence:
            piece += "\n```"
        parts.append(piece)
        buf = ["```"] if in_fence else []

    for line in lines:
        while len(line) > limit - 10:              # one absurdly long line
            if buf:
                flush()
            buf.append(line[:limit - 10])
            flush()
            line = line[limit - 10:]
        if buf and buflen(line) > limit - 8:
            flush()
        buf.append(line)
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
    tail = "\n".join(buf).strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


def clip(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def ago(dt: datetime) -> str:
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    for unit, n in (("y", 31536000), ("mo", 2592000), ("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= n:
            return f"{int(secs // n)}{unit}"
    return "just now"


_GREETINGS = {"hi", "hey", "hello", "yo", "sup", "hiya", "howdy", "gm",
              "good morning", "good evening", "hey there", "hello there"}
_THANKS = {"thanks", "thank you", "ty", "thx", "tysm", "cheers", "appreciate it",
           "thank u", "thankyou", "nice", "cool", "great"}


def canned_reply(question: str) -> str | None:
    q = question.strip().lower().strip("?.!, ")
    if q in _GREETINGS:
        return "Hey! \U0001f44b Ask me anything about agentty — I'll read the source and answer."
    if q in _THANKS:
        return "Anytime! \U0001f680"
    return None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.members = True
intents.message_content = True


class Bot(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions(
            everyone=False, roles=False, users=True, replied_user=False))
        self.tree = app_commands.CommandTree(self)
        self.owner_id = OWNER_ID_ENV
        self.invite_uses: dict[str, int] = {}
        self.started = False

    async def setup_hook(self) -> None:
        self.add_dynamic_items(Feedback)

    # helpers ---------------------------------------------------------------
    @property
    def guild(self) -> discord.Guild | None:
        return self.get_guild(GUILD_ID) if GUILD_ID else (self.guilds[0] if self.guilds else None)

    def chan(self, name: str) -> discord.TextChannel | None:
        g = self.guild
        return discord.utils.get(g.text_channels, name=name) if g else None

    async def owner(self) -> discord.User | None:
        if not self.owner_id:
            return None
        return self.get_user(self.owner_id) or await self.fetch_user(self.owner_id)

    async def notify(self, content: str | None = None, *, embed: discord.Embed | None = None,
                     embeds: list[discord.Embed] | None = None) -> None:
        """DM the server owner. Never raises."""
        kw: dict = {"content": content}
        if embeds:
            kw["embeds"] = embeds
        elif embed is not None:
            kw["embed"] = embed
        try:
            o = await self.owner()
            if o:
                await o.send(**kw)
        except Exception as exc:
            print(f"notify failed: {exc}")


client = Bot()
tree = client.tree


def is_owner(user: discord.abc.User) -> bool:
    return bool(client.owner_id) and user.id == client.owner_id


# ---------------------------------------------------------------------------
# Asking the agent
# ---------------------------------------------------------------------------

# bot answer message id -> (convo key, question). Lets a reply continue a chat.
answers: OrderedDict[int, tuple[str, str]] = OrderedDict()
# convo keys with live sessions, LRU — evicted ones are reset in the brain.
convos: OrderedDict[str, float] = OrderedDict()
MAX_CONVOS = 24


def remember_answer(msg_id: int, key: str, question: str) -> None:
    answers[msg_id] = (key, question)
    while len(answers) > 500:
        answers.popitem(last=False)


def touch_convo(key: str) -> None:
    convos[key] = time.monotonic()
    convos.move_to_end(key)
    while len(convos) > MAX_CONVOS:
        old, _ = convos.popitem(last=False)
        try:
            brain.reset(old)
        except Exception:
            pass


async def _safe_edit(msg: discord.Message, **kw) -> None:
    try:
        if "content" in kw and kw["content"] is not None:
            kw["content"] = kw["content"][:2000]
        await asyncio.wait_for(msg.edit(**kw), timeout=15)
    except (discord.HTTPException, asyncio.TimeoutError):
        pass


def feedback_view() -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    v.add_item(Feedback("up"))
    v.add_item(Feedback("down"))
    return v


async def stream_answer(question: str, *, channel: discord.abc.Messageable,
                        reply_to: discord.Message | None, key: str,
                        author: discord.abc.User) -> None:
    """Ask the agent and live-edit ONE message as the answer streams in, with a
    subtext line showing what the agent is reading. Then settle into the final
    chunked answer with 👍/👎."""
    st = {"text": "", "tool": "", "tools": 0, "dirty": False}

    def on_update(full: str) -> None:
        st["text"] = full
        st["dirty"] = True

    def on_tool(title: str) -> None:
        if title != st["tool"]:
            st["tools"] += 1
        st["tool"] = title
        st["dirty"] = True

    busy = brain._lock.locked()
    first = "-# \u23f3 queued behind another question…" if busy else "-# \U0001f9e0 thinking…"
    if reply_to is not None:
        msg = await reply_to.reply(first, mention_author=False)
    else:
        msg = await channel.send(first)

    def preview() -> str:
        raw = st["text"][:1800]
        shown = md_for_discord(raw)
        if shown.count("```") % 2:
            shown += "\n```"
        activity = f"-# \U0001f50e {clip(st['tool'], 90)}" if st["tool"] else "-# \U0001f9e0 thinking…"
        if not shown:
            return activity
        return f"{shown} \u258c\n{activity}"[:2000]

    async def pump() -> None:
        while True:
            await asyncio.sleep(1.6)
            if st["dirty"]:
                st["dirty"] = False
                await _safe_edit(msg, content=preview())

    pump_task = asyncio.create_task(pump())
    t0 = time.monotonic()
    try:
        async with channel.typing():
            answer = await brain.ask(question, on_update=on_update, on_tool=on_tool,
                                     convo_key=key)
    except Exception as exc:
        pump_task.cancel()
        await _safe_edit(msg, content=(
            f"\u26a0\ufe0f Something went wrong (`{type(exc).__name__}`). Try again in "
            f"a moment, or browse the docs: <{DOCS_URL}>"))
        return
    finally:
        pump_task.cancel()
    touch_convo(key)

    answer = (answer or "").strip()
    if not answer:
        await _safe_edit(msg, content=f"I didn't get an answer. Docs: <{DOCS_URL}>")
        return

    secs = time.monotonic() - t0
    foot = f"\n-# \U0001f50e checked the source in {secs:.0f}s · reply to keep going" if st["tools"] \
        else "\n-# reply to keep going"
    pieces = chunk(md_for_discord(answer), limit=2000 - len(foot))
    pieces[-1] += foot
    view = feedback_view()
    if len(pieces) == 1:
        await _safe_edit(msg, content=pieces[0], view=view)
        last = msg
    else:
        await _safe_edit(msg, content=pieces[0])
        last = msg
        for i, extra in enumerate(pieces[1:], 1):
            try:
                kw = {"view": view} if i == len(pieces) - 1 else {}
                last = await asyncio.wait_for(channel.send(content=extra, **kw), timeout=15)
            except (discord.HTTPException, asyncio.TimeoutError):
                pass
            await asyncio.sleep(0.3)
    for m in {msg.id, last.id}:
        remember_answer(m, key, question)


class Feedback(discord.ui.DynamicItem[discord.ui.Button], template=r"fb:(?P<v>up|down)"):
    """Persistent 👍/👎 on every answer — each vote is DM'd to the owner."""

    def __init__(self, v: str) -> None:
        super().__init__(discord.ui.Button(
            emoji="\U0001f44d" if v == "up" else "\U0001f44e",
            style=discord.ButtonStyle.secondary, custom_id=f"fb:{v}"))
        self.v = v

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["v"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Thanks — noted! \U0001f64f" if self.v == "up"
            else "Thanks — that goes straight to the maintainer. Reply to the answer with what was wrong and I'll try again.",
            ephemeral=True)
        m = interaction.message
        q = answers.get(m.id, ("", ""))[1] if m else ""
        if not q and m and m.reference and m.reference.message_id:
            try:
                q = (await m.channel.fetch_message(m.reference.message_id)).content
            except discord.HTTPException:
                pass
        e = discord.Embed(
            title=f"{'\U0001f44d' if self.v == 'up' else '\U0001f44e'} answer feedback from {interaction.user.display_name}",
            url=m.jump_url if m else None,
            color=0x3FB950 if self.v == "up" else 0xF85149,
            description=f"**Q:** {clip(q, 500) or '(unknown)'}\n\n**A:** {clip(m.content if m else '', 900)}")
        await client.notify(embed=e)


def convo_key_for(message: discord.Message) -> str:
    if isinstance(message.channel, discord.Thread):
        return f"t{message.channel.id}"
    ref = message.reference.message_id if message.reference else None
    if ref and ref in answers:
        return answers[ref][0]
    if message.guild is None:
        return f"dm{message.author.id}"
    return f"m{ref or message.id}"


def strip_mentions(text: str) -> str:
    for m in (f"<@{client.user.id}>", f"<@!{client.user.id}>"):
        text = text.replace(m, "")
    return text.strip()


async def with_reply_context(message: discord.Message, question: str) -> str:
    """When someone replies to a HUMAN message and pings the bot ("@agentty is
    this right?"), include the message being replied to."""
    ref = message.reference
    if not (ref and isinstance(ref.resolved, discord.Message)):
        return question
    r = ref.resolved
    if r.author.id == client.user.id:
        return question
    return f'(Replying to {r.author.display_name}: "{clip(r.content, 1500)}")\n\n{question}'


# ---------------------------------------------------------------------------
# #help: every question gets a thread + a first answer
# ---------------------------------------------------------------------------

help_askers: dict[int, int] = {}      # thread id -> asker id
help_handoff: set[int] = set()        # threads where another human stepped in


async def help_thread_asker(thread: discord.Thread) -> int | None:
    if thread.id in help_askers:
        return help_askers[thread.id]
    try:
        starter = thread.starter_message or await thread.parent.fetch_message(thread.id)
        help_askers[thread.id] = starter.author.id
        return starter.author.id
    except Exception:
        return None


async def handle_help_post(message: discord.Message) -> None:
    title = clip(re.sub(r"\s+", " ", message.clean_content), 90) or "help"
    try:
        thread = await message.create_thread(name=title, auto_archive_duration=1440)
    except discord.HTTPException:
        thread = None
    if thread:
        help_askers[thread.id] = message.author.id
        await stream_answer(message.content, channel=thread, reply_to=None,
                            key=f"t{thread.id}", author=message.author)
    else:
        await stream_answer(message.content, channel=message.channel, reply_to=message,
                            key=f"m{message.id}", author=message.author)


# ---------------------------------------------------------------------------
# GitHub: #123 unfurls, issue filing
# ---------------------------------------------------------------------------

_REF = re.compile(r"(?<![\w<&/#])#(\d{2,5})\b")
_CODE = re.compile(r"```.*?```|`[^`]*`", re.S)
_unfurled: dict[tuple[int, int], float] = {}     # (channel, number) -> time


def issue_embed(it: dict) -> discord.Embed:
    pr = "pull_request" in it
    if pr:
        merged = (it.get("pull_request") or {}).get("merged_at")
        icon, color = (("\U0001f7e3", 0x8957E5) if merged else
                       ("\U0001f534", 0xF85149) if it["state"] == "closed" else ("\U0001f7e2", 0x3FB950))
        kind = "PR"
    else:
        done = it.get("state_reason") == "completed"
        icon, color = (("\U0001f7e3", 0x8957E5) if it["state"] == "closed" and done else
                       ("\u26aa", 0x6E7681) if it["state"] == "closed" else ("\U0001f7e2", 0x3FB950))
        kind = "issue"
    labels = " ".join(f"`{l['name']}`" for l in it.get("labels", [])[:5])
    meta = f"{kind} by **{it['user']['login']}** · {it.get('comments', 0)} \U0001f4ac"
    e = discord.Embed(title=clip(f"{icon} #{it['number']} {it['title']}", 250),
                      url=it["html_url"], color=color,
                      description=f"{meta}{'  ' + labels if labels else ''}\n{clip(re.sub(r'<!--.*?-->', '', it.get('body') or '', flags=re.S), 280)}")
    return e


async def maybe_unfurl(message: discord.Message) -> None:
    text = _CODE.sub("", message.content)
    nums = list(dict.fromkeys(int(n) for n in _REF.findall(text)))[:3]
    embeds = []
    now = time.monotonic()
    for n in nums:
        if now - _unfurled.get((message.channel.id, n), 0) < 600:
            continue
        try:
            it = await github.issue(n)
        except Exception:
            continue
        if it and it.get("number") == n:
            _unfurled[(message.channel.id, n)] = now
            embeds.append(issue_embed(it))
    if embeds:
        try:
            await message.reply(embeds=embeds, mention_author=False, silent=True)
        except discord.HTTPException:
            pass


def new_issue_url(title: str, body: str) -> str:
    return f"{REPO_URL}/issues/new?title={quote(title[:200])}&body={quote(body[:6000])}"


class IssueModal(discord.ui.Modal, title="Send to GitHub issue"):
    def __init__(self, title_default: str, body_default: str) -> None:
        super().__init__(timeout=900)
        self.t = discord.ui.TextInput(label="Title", default=clip(title_default, 200), max_length=200)
        self.b = discord.ui.TextInput(label="Details", style=discord.TextStyle.paragraph,
                                      default=clip(body_default, 3900), max_length=4000, required=False)
        self.add_item(self.t)
        self.add_item(self.b)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        title, body = self.t.value.strip(), self.b.value.strip()
        if is_owner(interaction.user) and github.can_write:
            await interaction.response.defer(thinking=True)
            try:
                it = await github.create_issue(title, body)
            except Exception as exc:
                await interaction.followup.send(f"GitHub said no (`{exc}`). File it by hand: <{new_issue_url(title, body)}>")
                return
            await interaction.followup.send(f"\U0001f4ee Filed **#{it['number']}**", embed=issue_embed(it))
            return
        v = discord.ui.View()
        v.add_item(discord.ui.Button(label="Open the prefilled issue on GitHub", emoji="\U0001f4ee",
                                     url=new_issue_url(title, body)))
        await interaction.response.send_message(
            "Here's your issue, ready to submit under your own GitHub account:", view=v, ephemeral=True)


def message_to_issue(m: discord.Message) -> tuple[str, str]:
    title = clip(re.sub(r"\s+", " ", m.clean_content), 90) or "Report from Discord"
    body = (f"{m.clean_content}\n\n---\n_Reported by **{m.author.display_name}** on the agentty "
            f"Discord: {m.jump_url}_")
    if m.attachments:
        body += "\n\nAttachments:\n" + "\n".join(f"- {a.url}" for a in m.attachments[:5])
    return title, body


# ---------------------------------------------------------------------------
# Releases + stars
# ---------------------------------------------------------------------------

def release_embed(rel: dict) -> discord.Embed:
    body = rel.get("body") or ""
    heads = re.findall(r"^\s*[-*]\s+\*\*(.+?)\*\*", body, re.M)
    if heads:
        desc = "\n".join(f"\u2022 {clip(h.rstrip('.'), 140)}" for h in heads[:8])
        if len(heads) > 8:
            desc += f"\n\u2022 …and {len(heads) - 8} more"
    else:
        desc = clip(body, 900)
    tag = rel["tag_name"]
    e = discord.Embed(title=f"\U0001f680 agentty {tag}", url=rel["html_url"], color=BRAND,
                      description=desc or "New release.")
    e.add_field(name="Update", value="Run your install command again, e.g.\n"
                "```sh\ncurl -fsSL https://agentty.org/install.sh | sh\n```", inline=False)
    n = len(rel.get("assets", []))
    e.set_footer(text=f"{n} downloads ready · full notes on GitHub")
    if rel.get("published_at"):
        e.timestamp = datetime.fromisoformat(rel["published_at"].replace("Z", "+00:00"))
    return e


async def set_presence(tag: str | None = None, stars: int | None = None) -> None:
    tag = tag or state.get("release_tag") or ""
    stars = stars if stars is not None else state.get("stars", 0)
    name = " · ".join(x for x in (f"agentty {tag}" if tag else "", f"\u2605 {stars}" if stars else "") if x)
    try:
        await client.change_presence(activity=discord.Activity(
            type=discord.ActivityType.watching, name=name or "agentty"))
    except Exception:
        pass


async def release_loop() -> None:
    while True:
        try:
            rel = await github.latest_release(poll=True)
            if rel and rel.get("tag_name"):
                tag = rel["tag_name"]
                prev = state.get("release_tag")
                if prev != tag:
                    state.set("release_tag", tag)
                    await set_presence(tag=tag)
                    if prev is not None:
                        await announce_release(rel)
                    else:                       # first boot: seed #releases quietly
                        ch = client.chan(CH_RELEASES)
                        if ch and not [m async for m in ch.history(limit=1)]:
                            await ch.send(embed=release_embed(rel))
        except Exception as exc:
            print(f"release poll: {exc}")
        await asyncio.sleep(RELEASE_POLL)


async def announce_release(rel: dict) -> None:
    e = release_embed(rel)
    ch = client.chan(CH_RELEASES)
    posted = None
    if ch:
        posted = await ch.send(embed=e)
    camp = client.chan(CH_CAMPFIRE)
    if camp:
        where = posted.jump_url if posted else rel["html_url"]
        await camp.send(f"\U0001f680 **agentty {rel['tag_name']}** just dropped → {where}",
                        suppress_embeds=True)
    await client.notify(embed=e)


async def stars_loop() -> None:
    while True:
        try:
            repo = await github.repo(poll=True)
            if repo and "stargazers_count" in repo:
                stars = repo["stargazers_count"]
                mark = state.get("star_mark")
                cur = stars // STAR_STEP * STAR_STEP
                if mark is None:
                    state.set("star_mark", cur)
                elif cur > mark:
                    state.set("star_mark", cur)
                    camp = client.chan(CH_CAMPFIRE)
                    if camp:
                        await camp.send(f"\u2b50 **{cur} stars** on GitHub — thank you, pack. \U0001f43a")
                    await client.notify(f"\u2b50 agentty crossed **{cur}** stars.")
                if stars != state.get("stars"):
                    state.set("stars", stars)
                    await set_presence(stars=stars)
        except Exception as exc:
            print(f"stars poll: {exc}")
        await asyncio.sleep(300)


# ---------------------------------------------------------------------------
# Pulse: what is the community saying?
# ---------------------------------------------------------------------------

PULSE_PROMPT = (
    "TASK (overrides the usual answer style): you are briefing the agentty "
    "maintainer on what his Discord community said. Below is the raw chat "
    "transcript (each line ends with its message link). Do NOT use tools unless "
    "a claim genuinely needs the code. Write a tight Discord-markdown briefing "
    "with these sections, skipping any that are empty:\n"
    "**\U0001f525 Vibe** (one or two lines), **\U0001f41b Bugs / breakage** (who, "
    "what, link), **\U0001f4a1 Requests & ideas**, **\U0001f615 Confusion / docs "
    "gaps**, **\U0001f64c Praise**, **\u23f0 Needs your reply** (unanswered "
    "questions, with links). Be concrete, name people, keep it under 350 words. "
    "If nothing meaningful happened, say so in one line.\n\nTRANSCRIPT:\n"
)


async def collect_chat(hours: float, limit_chars: int = 40000) -> tuple[str, int]:
    g = client.guild
    if not g:
        return "", 0
    after = datetime.now(timezone.utc) - timedelta(hours=hours)
    lines: list[tuple[datetime, str]] = []
    sources: list[discord.abc.Messageable] = []
    for name in PULSE_CHANNELS:
        ch = client.chan(name)
        if ch:
            sources.append(ch)
            sources.extend(t for t in ch.threads if t.last_message_id)
    for src in sources:
        try:
            async for m in src.history(limit=400, after=after, oldest_first=True):
                if m.author.bot or m.webhook_id or not (m.content or m.attachments):
                    continue
                where = src.name if not isinstance(src, discord.Thread) else f"{src.parent.name}/{src.name}"
                txt = clip(m.clean_content, 600) + (" [+attachment]" if m.attachments else "")
                lines.append((m.created_at, f"[#{where}] {m.author.display_name}: {txt} <{m.jump_url}>"))
        except discord.HTTPException:
            continue
    lines.sort(key=lambda x: x[0])
    text = "\n".join(l for _, l in lines)
    return text[-limit_chars:], len(lines)


async def run_pulse(hours: float) -> str:
    text, n = await collect_chat(hours)
    if n == 0:
        return f"\U0001f319 Quiet night — no human messages in the last {hours:g}h."
    key = f"pulse{int(time.time())}"
    try:
        out = await brain.ask(PULSE_PROMPT + text, convo_key=key)
    finally:
        try:
            brain.reset(key)
        except Exception:
            pass
    return f"**Pulse — last {hours:g}h · {n} messages**\n\n{(out or '').strip()}"


async def daily_pulse_loop() -> None:
    if not PULSE_HOUR.strip():
        return
    hour = int(PULSE_HOUR)
    while True:
        now = datetime.now()
        nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        day = datetime.now().strftime("%Y-%m-%d")
        if state.get("pulse_day") == day:
            continue
        state.set("pulse_day", day)
        try:
            text, n = await collect_chat(24)
            if n:
                for piece in chunk(md_for_discord(await run_pulse(24))):
                    await client.notify(piece)
        except Exception as exc:
            print(f"daily pulse: {exc}")


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@client.event
async def on_ready() -> None:
    g = client.guild
    if g and not client.owner_id:
        client.owner_id = g.owner_id
    if client.started:
        return
    client.started = True
    if g:
        tree.copy_global_to(guild=g)
        await tree.sync(guild=g)
        try:
            client.invite_uses = {i.code: i.uses or 0 for i in await g.invites()}
        except discord.HTTPException:
            pass
    print(f"agentty-bot online as {client.user} (guild: {g and g.name}, owner: {client.owner_id})")
    await set_presence()
    for coro in (release_loop(), stars_loop(), daily_pulse_loop()):
        asyncio.create_task(coro)
    try:
        await brain._ensure_started()
        print("agentty-bot brain: ACP agent ready")
    except Exception as exc:
        print(f"agentty-bot brain: NOT ready ({exc}) — will retry on demand")


@client.event
async def on_invite_create(invite: discord.Invite) -> None:
    client.invite_uses[invite.code] = invite.uses or 0


async def which_invite(g: discord.Guild) -> discord.Invite | None:
    try:
        now = await g.invites()
    except discord.HTTPException:
        return None
    used = None
    for inv in now:
        if (inv.uses or 0) > client.invite_uses.get(inv.code, 0):
            used = inv
    client.invite_uses = {i.code: i.uses or 0 for i in now}
    return used


@client.event
async def on_member_join(member: discord.Member) -> None:
    if member.bot:
        return
    inv = await which_invite(member.guild)
    try:
        await member.send(WELCOME_DM)
    except discord.HTTPException:
        pass
    camp = client.chan(CH_CAMPFIRE)
    if camp:
        await camp.send(random.choice(CAMPFIRE_WELCOMES).format(m=member.mention) + WELCOME_TAIL)
    fresh = state.get("fresh", [])
    state.set("fresh", (fresh + [member.id])[-200:])
    if state.get("notify_joins", True):
        age = ago(member.created_at)
        e = discord.Embed(title=f"\U0001f44b {member.display_name} joined",
                          color=0x3FB950, timestamp=datetime.now(timezone.utc),
                          description=f"{member.mention} · `@{member.name}` · `{member.id}`")
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="Account age", value=age + (" \u26a0\ufe0f new" if
                    datetime.now(timezone.utc) - member.created_at < timedelta(days=7) else ""))
        e.add_field(name="Member #", value=str(member.guild.member_count))
        if inv:
            who = inv.inviter.name if inv.inviter else "?"
            e.add_field(name="Invite", value=f"`{inv.code}` by {who} ({inv.uses} uses)")
        await client.notify(embed=e)


@client.event
async def on_member_remove(member: discord.Member) -> None:
    if member.bot or not state.get("notify_joins", True):
        return
    stayed = ago(member.joined_at) if member.joined_at else "?"
    await client.notify(f"\U0001f6aa **{member.display_name}** (`@{member.name}`) left after {stayed}. "
                        f"{member.guild.member_count} members now.")


async def forward_github(message: discord.Message) -> None:
    mode = state.get("notify_github", "all")
    if mode == "off" or not message.embeds:
        return
    out = []
    for e in message.embeds:
        actor = (e.author.name or "") if e.author else ""
        if mode == "others" and actor.lower() == OWNER_GH.lower():
            continue
        out.append(discord.Embed.from_dict(e.to_dict()))
    if out:
        await client.notify(embeds=out[:10])


@client.event
async def on_message(message: discord.Message) -> None:
    if client.user is None or message.author.id == client.user.id:
        return
    ch = message.channel
    chname = getattr(ch, "name", "")

    # GitHub webhook posts → owner DM.
    if message.webhook_id:
        if chname in (CH_GITHUB, CH_RELEASES):
            await forward_github(message)
        return
    if message.author.bot:
        return

    # A newcomer's first words → owner DM.
    if message.guild and message.author.id in state.get("fresh", []):
        state.set("fresh", [i for i in state.get("fresh", []) if i != message.author.id])
        await client.notify(f"\U0001f4ac **{message.author.display_name}** said their first words in "
                            f"#{chname}: {clip(message.clean_content, 300)}\n{message.jump_url}")

    is_dm = message.guild is None
    mentioned = client.user in message.mentions and not message.mention_everyone
    replying_to_bot = bool(message.reference and isinstance(message.reference.resolved, discord.Message)
                           and message.reference.resolved.author.id == client.user.id)
    question = strip_mentions(message.content)

    # #help: top-level posts get a thread + an answer.
    if chname == CH_HELP and not isinstance(ch, discord.Thread) and not replying_to_bot and question:
        await handle_help_post(message)
        return

    # Inside a #help thread: keep helping the asker until another human steps in.
    in_help_thread = isinstance(ch, discord.Thread) and ch.parent and ch.parent.name == CH_HELP \
        and ch.owner_id == client.user.id
    if in_help_thread and not (mentioned or replying_to_bot):
        asker = await help_thread_asker(ch)
        if message.author.id != asker:
            help_handoff.add(ch.id)
            return
        if ch.id in help_handoff:
            return
        if question:
            await stream_answer(question, channel=ch, reply_to=message, key=f"t{ch.id}",
                                author=message.author)
        return

    if is_dm or mentioned or replying_to_bot:
        if not question:
            await message.reply("Ask me anything about agentty \U0001f680 e.g. *how do I enable Smart "
                                "Mode?* or *how does the local RAG work?*", mention_author=False)
            return
        canned = canned_reply(question)
        if canned:
            await message.reply(canned, mention_author=False)
            return
        if message.attachments:
            question += "\n\n(Attached: " + ", ".join(a.filename for a in message.attachments[:5]) + ")"
        question = await with_reply_context(message, question)
        await stream_answer(question, channel=ch, reply_to=message, key=convo_key_for(message),
                            author=message.author)
        return

    # Everyone else's chat: just unfurl #123 refs.
    if message.guild and "#" in message.content:
        await maybe_unfurl(message)


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

def _docs_index() -> list[tuple[str, str, str]]:
    """(title, url, description) for every page under docs/website/."""
    out = []
    d = Path(ACP_WORKSPACE) / "docs" / "website"
    for p in sorted(d.glob("*.md")):
        if p.name == "README.md":
            continue
        fm = {}
        txt = p.read_text(errors="ignore")
        if txt.startswith("---"):
            for line in txt.split("---", 2)[1].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    fm[k.strip()] = v.strip().strip("\"'")
        slug = fm.get("slug", p.stem)
        url = DOCS_URL if p.stem == "index" or not slug else f"{DOCS_URL}{slug}/"
        out.append((fm.get("title", slug), url, fm.get("description", "")))
    return out


DOCS = _docs_index()


@tree.command(name="ask", description="Ask the agentty agent anything (it reads the real source)")
@app_commands.describe(question="Your question about agentty")
async def ask_cmd(interaction: discord.Interaction, question: str) -> None:
    await interaction.response.send_message(f"**{interaction.user.display_name} asked:** {clip(question, 1800)}")
    q_msg = await interaction.original_response()
    await stream_answer(question, channel=interaction.channel, reply_to=q_msg,
                        key=f"m{q_msg.id}", author=interaction.user)


@tree.command(name="docs", description="Find an agentty docs page")
@app_commands.describe(topic="Start typing — e.g. smart mode, sandbox, providers")
async def docs_cmd(interaction: discord.Interaction, topic: str | None = None) -> None:
    if not topic:
        await interaction.response.send_message(f"\U0001f4d6 <{DOCS_URL}>")
        return
    t = topic.lower()
    hit = next((d for d in DOCS if d[1] == topic), None) or \
        next((d for d in DOCS if t in d[0].lower() or t in d[2].lower()), None)
    if not hit:
        await interaction.response.send_message(
            f"No page matches *{topic}* — try `/ask` or browse <{DOCS_URL}>", ephemeral=True)
        return
    e = discord.Embed(title=f"\U0001f4d6 {hit[0]}", url=hit[1], description=hit[2], color=BRAND)
    await interaction.response.send_message(embed=e)


@docs_cmd.autocomplete("topic")
async def docs_ac(interaction: discord.Interaction, current: str):
    c = current.lower()
    hits = [d for d in DOCS if c in d[0].lower() or c in d[2].lower()] if c else DOCS
    return [app_commands.Choice(name=clip(f"{d[0]} — {d[2]}", 100), value=d[1]) for d in hits[:25]]


@tree.command(name="install", description="How to install (or update) agentty")
@app_commands.rename(platform="os")
@app_commands.choices(platform=[app_commands.Choice(name=v[0], value=k) for k, v in INSTALL.items()])
async def install_cmd(interaction: discord.Interaction, platform: app_commands.Choice[str] | None = None) -> None:
    keys = [platform.value] if platform else list(INSTALL)
    e = discord.Embed(title="\u2b07\ufe0f Install agentty", url=INSTALL_URL, color=BRAND,
                      description="Run it again any time to update.")
    for k in keys:
        name, cmd = INSTALL[k]
        e.add_field(name=name, value=f"```sh\n{cmd}\n```", inline=False)
    await interaction.response.send_message(embed=e)


@tree.command(name="release", description="What's new in the latest (or a given) agentty release")
@app_commands.describe(version="e.g. v0.9.64 — leave empty for the latest")
async def release_cmd(interaction: discord.Interaction, version: str | None = None) -> None:
    await interaction.response.defer()
    try:
        if version:
            v = version if version.startswith("v") else f"v{version}"
            rel = await github.get(f"/repos/{REPO}/releases/tags/{v}", ttl=300)
        else:
            rel = await github.latest_release()
    except Exception:
        rel = None
    if not rel:
        await interaction.followup.send(f"Couldn't find that release. All releases: <{REPO_URL}/releases>")
        return
    await interaction.followup.send(embed=release_embed(rel))


@release_cmd.autocomplete("version")
async def release_ac(interaction: discord.Interaction, current: str):
    try:
        rels = await github.releases(25)
    except Exception:
        return []
    return [app_commands.Choice(name=r["tag_name"], value=r["tag_name"])
            for r in rels if current.lower() in r["tag_name"].lower()][:25]


@tree.command(name="stats", description="agentty by the numbers")
async def stats_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    try:
        repo, rel, dl = await asyncio.gather(github.repo(), github.latest_release(), github.download_count())
    except Exception as exc:
        await interaction.followup.send(f"GitHub is being shy right now (`{type(exc).__name__}`).")
        return
    e = discord.Embed(title="\U0001f4ca agentty by the numbers", url=REPO_URL, color=BRAND)
    e.add_field(name="\u2b50 Stars", value=f"{repo['stargazers_count']:,}")
    e.add_field(name="\U0001f374 Forks", value=f"{repo['forks_count']:,}")
    e.add_field(name="\u2b07\ufe0f Release downloads", value=f"{dl:,}")
    e.add_field(name="\U0001f680 Latest", value=f"[{rel['tag_name']}]({rel['html_url']}) · {ago(datetime.fromisoformat(rel['published_at'].replace('Z', '+00:00')))} ago")
    e.add_field(name="\U0001f7e2 Open issues + PRs", value=str(repo["open_issues_count"]))
    if interaction.guild:
        e.add_field(name="\U0001f525 Around the fire", value=f"{interaction.guild.member_count} people")
    await interaction.followup.send(embed=e)


@tree.command(name="issue", description="Show a GitHub issue or PR")
@app_commands.describe(number="Issue or PR number")
async def issue_cmd(interaction: discord.Interaction, number: app_commands.Range[int, 1, 999999]) -> None:
    await interaction.response.defer()
    try:
        it = await github.issue(number)
    except Exception:
        it = None
    if not it:
        await interaction.followup.send(f"No #{number} in {REPO}.")
        return
    await interaction.followup.send(embed=issue_embed(it))


@tree.command(name="search", description="Search agentty's GitHub issues & PRs")
@app_commands.describe(query="Words to look for", status="open, closed, or any")
@app_commands.choices(status=[app_commands.Choice(name=s, value=s) for s in ("open", "closed", "any")])
async def search_cmd(interaction: discord.Interaction, query: str,
                     status: app_commands.Choice[str] | None = None) -> None:
    await interaction.response.defer()
    q = query + ("" if not status or status.value == "any" else f" is:{status.value}")
    try:
        items = await github.search_issues(q, 8)
    except Exception:
        items = []
    if not items:
        await interaction.followup.send(f"Nothing for *{query}*. [Open a new issue?]({REPO_URL}/issues/new/choose)",
                                        suppress_embeds=True)
        return
    rows = []
    for it in items:
        kind = "PR" if "pull_request" in it else "issue"
        dot = "\U0001f7e2" if it["state"] == "open" else "\U0001f7e3"
        rows.append(f"{dot} [#{it['number']} {clip(it['title'], 80)}]({it['html_url']}) -# {kind} · {it['user']['login']}")
    e = discord.Embed(title=f"\U0001f50e {clip(query, 80)}", color=BRAND,
                      url=f"{REPO_URL}/issues?q={quote(q)}", description="\n".join(rows))
    await interaction.followup.send(embed=e)


@tree.command(name="bug", description="File a bug on GitHub straight from Discord")
async def bug_cmd(interaction: discord.Interaction) -> None:
    await interaction.response.send_modal(IssueModal(
        "", "**What happened**\n\n**What I expected**\n\n**Steps**\n1. \n\n"
            "`agentty --version`: \nOS / terminal: "))


@tree.context_menu(name="Send to GitHub issue")
async def to_issue_menu(interaction: discord.Interaction, message: discord.Message) -> None:
    await interaction.response.send_modal(IssueModal(*message_to_issue(message)))


@tree.command(name="pulse", description="(owner) What has the community been saying?")
@app_commands.describe(hours="How far back (default 24)")
@app_commands.default_permissions(administrator=True)
async def pulse_cmd(interaction: discord.Interaction, hours: app_commands.Range[int, 1, 336] = 24) -> None:
    if not is_owner(interaction.user):
        await interaction.response.send_message("That one's for the maintainer \U0001f319", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        out = await run_pulse(hours)
    except Exception as exc:
        out = f"Pulse failed: `{exc}`"
    for piece in chunk(md_for_discord(out)):
        await interaction.followup.send(piece, ephemeral=True, suppress_embeds=True)


@tree.command(name="notify", description="(owner) What should I DM you about?")
@app_commands.rename(gh_mode="github")
@app_commands.describe(gh_mode="GitHub events from #github/#releases", joins="Joins, leaves, first messages")
@app_commands.choices(gh_mode=[app_commands.Choice(name="everything", value="all"),
                              app_commands.Choice(name="everything except my own", value="others"),
                              app_commands.Choice(name="nothing", value="off")])
@app_commands.default_permissions(administrator=True)
async def notify_cmd(interaction: discord.Interaction, gh_mode: app_commands.Choice[str] | None = None,
                     joins: bool | None = None) -> None:
    if not is_owner(interaction.user):
        await interaction.response.send_message("That one's for the maintainer \U0001f319", ephemeral=True)
        return
    if gh_mode:
        state.set("notify_github", gh_mode.value)
    if joins is not None:
        state.set("notify_joins", joins)
    gmode = {"all": "everything", "others": "everything except your own", "off": "nothing"}[state.get("notify_github")]
    await interaction.response.send_message(
        f"\U0001f514 GitHub: **{gmode}** · joins/leaves/first messages: **{'on' if state.get('notify_joins') else 'off'}**",
        ephemeral=True)


@tree.command(name="help", description="What can this bot do?")
async def help_cmd(interaction: discord.Interaction) -> None:
    e = discord.Embed(title="agentty bot", color=BRAND, description=(
        "I'm agentty itself, running against its own source. I read the code before I answer.\n\n"
        "**Ask:** @mention me, reply to me, DM me, or `/ask`. Reply to my answer to keep going.\n"
        "**#help:** post a question and you get a thread with an answer right away.\n"
        "**GitHub:** write `#123` to show an issue, or right-click a message → Apps → "
        "*Send to GitHub issue*.\n\n"
        "`/docs` `/install` `/release` `/stats` `/issue` `/search` `/bug`"))
    await interaction.response.send_message(embed=e, ephemeral=True)


def main() -> None:
    if not TOKEN:
        raise SystemExit("DISCORD_BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")
    client.run(TOKEN)


if __name__ == "__main__":
    main()
