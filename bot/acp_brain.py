#!/usr/bin/env python3
"""ACP brain for the agentty community bot.

Drives a persistent `agentty acp` subprocess over stdio (the same Agent Client
Protocol Zed speaks) and exposes one async coroutine — `AcpBrain.ask(question)`
— that returns agentty's natural-language answer.

Why this is the *smart* backend: agentty is itself an AI coding agent, already
authenticated on this box (OAuth). We don't reimplement intelligence or ship an
API key — we let the real agent answer, grounded in reality, using its own
login. The bot just relays.

Economy model — ONE WARM SESSION, FRESH THREAD ON FULL (never compaction):
We keep a single ACP session alive across questions. Its prompt prefix (the big
system prompt + tool schemas + prior turns) stays warm in the provider's prompt
cache, so every turn re-reads that prefix at ~10% of fresh-input price instead
of paying for it cold. The full framing preamble is sent only on the FIRST turn
of a session; later turns send just the question (the framing already lives in
session history). We watch the per-turn `usage_update` (used/size); when the
context crosses ~80% full we RETIRE the session and open a brand-new one on the
next question. We deliberately DO NOT compact — a fresh `session/new` is nearly
free, whereas compaction runs an extra summary request; the bot's questions are
self-contained enough that losing old chat history costs nothing.

Safety: the ACP session runs in the **Ask** profile and this client AUTO-DENIES
every permission request (file writes, bash, network side-effects). A Discord
user therefore can never make agentty edit files or run commands on the host —
it can only read/reason/answer. Prompts are serialised (one turn at a time)
behind an asyncio.Lock because an ACP turn is stateful.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Optional


AGENTTY_BIN = os.environ.get("AGENTTY_BIN", "agentty")
# Workspace handed to the agent = the full agentty source tree. The `acp -w`
# flag sandboxes filesystem tools to this dir, so the agent can freely
# read/grep/repo_map the ENTIRE codebase to ground its answers — and can touch
# nothing outside it.
ACP_WORKSPACE = os.environ.get("ACP_WORKSPACE", "/home/ayush/projects/agentty")
# Answer model — Haiku: fast, cheap, and plenty smart for grounded Q&A.
ACP_MODEL = os.environ.get("ACP_MODEL", "claude-haiku-4-5")
# Per-question wall-clock ceiling — generous, a real turn reads several files.
ACP_TIMEOUT = float(os.environ.get("ACP_TIMEOUT", "180"))
# When a warm session's context passes this fraction of the model window, we
# retire it and open a FRESH session on the next question (never compaction).
ACP_CONTEXT_FILL = float(os.environ.get("ACP_CONTEXT_FILL", "0.80"))

# System framing prepended to every user question. The agent IS agentty, run in
# the Ask profile inside the agentty source tree, so it can actually read the
# code before answering — we WANT it to. Write/exec/net still prompt and this
# client auto-denies those, so a Discord user can only ever make it read+reason.
PREAMBLE = (
    "You are the official agentty community assistant, answering a question in "
    "the agentty Discord. agentty is a fast, native (C++23), single-binary "
    "terminal coding agent with local hybrid RAG, Smart Mode model routing, "
    "OS sandboxing, ACP/MCP support, and skills.\n\n"
    "IMPORTANT: your working directory IS the complete agentty source tree. Use "
    "your tools freely and thoroughly — grep, read, repo_map, find_definition, "
    "glob — to ground EVERY factual claim in the ACTUAL code, docs (docs/, "
    "docs/website/), README, and CHANGELOG before you answer. Never guess about "
    "a flag, a default, a file path, or how a feature works when you can read "
    "it in seconds. Prefer the real source of truth over memory. You may answer "
    "ANY question about agentty in full depth — architecture, internals, CLI "
    "usage, config, building, providers, tools — as well as general coding "
    "questions.\n\n"
    "Answer style: write for a Discord chat message. Be direct and genuinely "
    "helpful. Use compact GitHub-flavoured markdown and fenced code blocks for "
    "commands/code. Cite concrete file paths (e.g. `src/acp/server.cpp`) and "
    "links to https://agentty.org/docs when useful. Length: as long as the "
    "question needs and no longer — a one-liner for a simple question, a full "
    "walkthrough for a deep one. Do NOT edit files or run shell commands; only "
    "read, reason, and explain. If after checking you still don't know, say so "
    "honestly and point to the docs.\n\n"
    "BE TOKEN-FRUGAL — this runs on a metered API. Use the CHEAPEST path that "
    "answers correctly: prefer `search_docs` and a single targeted `grep` (with "
    "a tight glob/path) or `repo_map` over broad exploration. Read only the "
    "specific slice of a file you need, never whole large files. Do NOT open "
    "files you don't need, don't re-read, and don't run a tool 'just to be "
    "sure'. The moment you have enough to answer, STOP and answer. Aim to "
    "resolve most questions in 0–3 tool calls. Keep the answer as short as the "
    "question allows (a sentence or two for simple asks; only go long when the "
    "question genuinely needs depth), and never pad.\n\n"
    "User question:\n"
)

# Sent instead of the full PREAMBLE on FOLLOW-UP turns of a warm session — the
# framing already lives in the session's history, so we don't re-pay for it.
REMINDER = (
    "(Same agentty-community-assistant rules as before: ground answers in the "
    "real code with minimal tool calls, be token-frugal, Discord-friendly "
    "markdown.)\n\nUser question:\n"
)


# Short conversational lead-ins the agent emits WHILE working ("Let me search
# the codebase…"). They're great as live-stream feedback but noise in the final
# answer, so we trim them off the front of the composed reply.
_NARRATION = re.compile(
    r"^\s*(?:(?:I'?ll|I will|Let me|Now(?:,| let me)?|First,?|Next,?|"
    r"Perfect[.!]?|Great[.!]?|Good[.!]?|Okay[.!]?|Sure[.!]?|Alright[.!]?|"
    r"Looking at|Searching|Checking|Reading|Let's)\b[^\n]*?[.:!]\s*)",
    re.IGNORECASE,
)


def _clean_answer(text: str) -> str:
    """Return just the substantive answer, minus the agent's working narration.

    agentty streams its interstitial thoughts ("Let me look at…", "Perfect!")
    as message chunks too. If it composed a final answer after a markdown
    horizontal rule (`---`), keep only what follows the LAST one; otherwise peel
    off leading narration sentences until real content begins.
    """
    text = (text or "").strip()
    if not text:
        return ""
    # Agentty typically emits `---` right before the composed final answer.
    parts = re.split(r"\n\s*-{3,}\s*\n", text)
    if len(parts) > 1 and len(parts[-1].strip()) > 40:
        return parts[-1].strip()
    # Otherwise strip run-on narration lead-ins from the front.
    prev = None
    while prev != text:
        prev = text
        text = _NARRATION.sub("", text, count=1).lstrip()
    return text.strip()


class AcpBrain:
    """Manages one long-lived `agentty acp` process and serialises prompts."""

    def __init__(self) -> None:
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._lock = asyncio.Lock()          # one turn at a time
        self._start_lock = asyncio.Lock()    # guard (re)spawn
        self._next_id = 1
        self._reader_task: Optional[asyncio.Task] = None
        # Pending JSON-RPC responses keyed by id -> asyncio.Future
        self._pending: dict[int, asyncio.Future] = {}
        # Accumulated agent text for the in-flight prompt (session id -> list).
        self._answer: dict[str, str] = {}
        # Optional live-stream callback per session (session id -> callable(str)).
        self._stream_cbs: dict[str, object] = {}
        # Optional tool-activity callback per session (session id -> callable(str)):
        # fired with a short human label each time the agent starts a tool call.
        self._tool_cbs: dict[str, object] = {}
        # ── keyed session reuse ──
        # Conversation key (e.g. "global" or a Discord thread id) -> live ACP
        # session id. Each key keeps its OWN warm, prompt-cache-hot session, so
        # a thread has real conversational memory while separate keys stay
        # isolated. None-valued/absent keys open a fresh session on next ask.
        self._sessions: dict[str, str] = {}
        # Reverse map (session id -> conversation key) for retirement.
        self._sid_key: dict[str, str] = {}
        # Latest context fill reported per session id, as (used, size).
        self._usage: dict[str, tuple[int, int]] = {}
        self._ready = False

    # ── process lifecycle ────────────────────────────────────────────────
    async def _ensure_started(self) -> None:
        if self._proc and self._proc.returncode is None:
            return
        async with self._start_lock:
            if self._proc and self._proc.returncode is None:
                return
            os.makedirs(ACP_WORKSPACE, exist_ok=True)
            self._proc = await asyncio.create_subprocess_exec(
                AGENTTY_BIN, "acp",
                "-w", ACP_WORKSPACE,
                "-m", ACP_MODEL,
                "-p", "ask",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                # ACP streams big tool results / file contents as single JSON
                # lines that easily exceed asyncio's default 64 KiB readline
                # limit (which would raise and kill the reader). Give it room.
                limit=32 * 1024 * 1024,
            )
            self._pending.clear()
            self._answer.clear()
            self._sessions.clear()
            self._sid_key.clear()
            self._usage.clear()
            self._reader_task = asyncio.create_task(self._read_loop())
            # Handshake.
            init = await self._request("initialize", {
                "protocolVersion": 1,
                "clientCapabilities": {},
            })
            # If agentty advertises auth methods, it isn't logged in.
            if init.get("authMethods"):
                raise RuntimeError(
                    "agentty is not authenticated — run `agentty login` on the host"
                )
            self._ready = True

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        try:
            while True:
                try:
                    line = await self._proc.stdout.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    # A single line exceeded the stream limit. Drain raw bytes
                    # up to the next newline and keep the reader alive instead
                    # of tearing everything down.
                    while True:
                        try:
                            chunk = await self._proc.stdout.read(65536)
                        except Exception:
                            chunk = b""
                        if not chunk or b"\n" in chunk:
                            break
                    continue
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._dispatch(msg)
        except Exception:
            pass
        finally:
            # Fail any waiters so callers don't hang if the process died.
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError("agentty acp process exited"))
            self._pending.clear()
            self._ready = False

    def _dispatch(self, msg: dict) -> None:
        mid = msg.get("id")
        method = msg.get("method")

        # 1. Response to one of our requests.
        if mid is not None and method is None:
            fut = self._pending.pop(mid, None)
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(RuntimeError(
                        msg["error"].get("message", "acp error")))
                else:
                    fut.set_result(msg.get("result", {}))
            return

        # 2. Server -> client REQUEST (needs a reply). The one we must handle
        #    is session/request_permission: always DENY (read-only bot).
        if mid is not None and method is not None:
            self._handle_server_request(mid, method, msg.get("params", {}))
            return

        # 3. Notification (no id): session/update carries streamed text.
        if method == "session/update":
            self._handle_update(msg.get("params", {}))

    def _handle_server_request(self, mid, method: str, params: dict) -> None:
        result: dict
        if method == "session/request_permission":
            # Deny every permission: pick the reject/cancel option if offered,
            # else signal cancellation. This keeps the host safe from Discord.
            opt_id = None
            for opt in params.get("options", []):
                if opt.get("kind") in ("reject_once", "reject_always"):
                    opt_id = opt.get("optionId")
                    break
            if opt_id is not None:
                result = {"outcome": {"outcome": "selected", "optionId": opt_id}}
            else:
                result = {"outcome": {"outcome": "cancelled"}}
        elif method in ("fs/read_text_file",):
            # We don't grant filesystem access.
            self._reply_error(mid, "filesystem access is disabled for this agent")
            return
        else:
            # Unknown client-bound request: reply with an empty object so the
            # agent isn't left hanging.
            result = {}
        self._reply(mid, result)

    def _handle_update(self, params: dict) -> None:
        sid = params.get("sessionId", "")
        upd = params.get("update", {})
        kind = upd.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = upd.get("content", {})
            if content.get("type") == "text":
                piece = content.get("text", "")
                # Maintain a running string (O(1) append) — NEVER re-join the
                # whole list per chunk (that is O(n²) and starves the event loop
                # on big replies, which stalls the Discord gateway heartbeat).
                self._answer[sid] = self._answer.get(sid, "") + piece
                cb = self._stream_cbs.get(sid)
                if cb is not None:
                    try:
                        cb(self._answer[sid])
                    except Exception:
                        pass
        elif kind == "usage_update":
            # Per-turn context fill: used tokens / model window size. Drives the
            # "retire the session when nearly full" decision (no compaction).
            try:
                used = int(upd.get("used", 0))
                size = int(upd.get("size", 0))
                if size > 0:
                    self._usage[sid] = (used, size)
            except (TypeError, ValueError):
                pass
        elif kind in ("tool_call", "tool_call_update"):
            # The agent started/updated a tool call — surface a short activity
            # label (e.g. "Read src/acp/server.cpp"). acp-cpp flattens the arm,
            # so title/kind/status sit on the update object directly; the title
            # often arrives on the follow-up tool_call_update, not the first.
            title = upd.get("title") or ""
            cb = self._tool_cbs.get(sid)
            if cb is not None and title:
                try:
                    cb(str(title))
                except Exception:
                    pass

    # ── low-level JSON-RPC ───────────────────────────────────────────────
    def _write(self, obj: dict) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write((json.dumps(obj) + "\n").encode())

    def _reply(self, mid, result: dict) -> None:
        self._write({"jsonrpc": "2.0", "id": mid, "result": result})

    def _reply_error(self, mid, message: str) -> None:
        self._write({"jsonrpc": "2.0", "id": mid,
                     "error": {"code": -32000, "message": message}})

    async def _request(self, method: str, params: dict) -> dict:
        mid = self._next_id
        self._next_id += 1
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        self._write({"jsonrpc": "2.0", "id": mid, "method": method,
                     "params": params})
        return await fut

    # ── public API ───────────────────────────────────────────────────────
    async def ask(self, question: str, on_update=None, on_tool=None,
                  convo_key: str = "global") -> str:
        """Ask the agent a question; return its answer text (may raise).

        ``on_update(str)`` — called with the growing answer as chunks stream in.
        ``on_tool(str)``   — called with a short label each time the agent starts
                             a tool call (e.g. "Read src/acp/server.cpp").
        ``convo_key``      — conversation bucket. The same key reuses one warm,
                             prompt-cache-hot session (real memory); different
                             keys stay isolated. Use a Discord thread id for
                             per-thread memory, or "global" for one-off asks.
        """
        question = question.strip()
        if not question:
            return ""
        async with self._lock:
            await self._ensure_started()
            # Reuse this key's warm session if present; else open a fresh one.
            # First turn gets the full framing PREAMBLE; later turns get only a
            # tiny REMINDER (the framing already lives in cached history).
            sid = self._sessions.get(convo_key)
            if sid is None:
                new = await self._request("session/new",
                                          {"cwd": ACP_WORKSPACE, "mcpServers": []})
                sid = new["sessionId"]
                self._sessions[convo_key] = sid
                self._sid_key[sid] = convo_key
                framing = PREAMBLE
            else:
                framing = REMINDER
            self._answer[sid] = ""
            if on_update is not None:
                self._stream_cbs[sid] = on_update
            if on_tool is not None:
                self._tool_cbs[sid] = on_tool
            try:
                await asyncio.wait_for(
                    self._request("session/prompt", {
                        "sessionId": sid,
                        "prompt": [{"type": "text", "text": framing + question}],
                    }),
                    timeout=ACP_TIMEOUT,
                )
            except asyncio.TimeoutError:
                try:
                    self._write({"jsonrpc": "2.0", "method": "session/cancel",
                                 "params": {"sessionId": sid}})
                except Exception:
                    pass
                text = self._answer.pop(sid, "")
                self._retire(sid)   # timeout may have left the turn half-applied
                return text or "⏳ That took too long — try a more specific question."
            except Exception:
                # Any prompt error (incl. context overflow) — retire so the next
                # question opens a fresh thread, then re-raise for the caller.
                self._retire(sid)
                raise
            finally:
                self._stream_cbs.pop(sid, None)
                self._tool_cbs.pop(sid, None)

            answer = _clean_answer(self._answer.pop(sid, ""))
            # If context is now nearly full, retire this key's session so the
            # NEXT question on this key opens a brand-new thread. Never compact.
            used, size = self._usage.get(sid, (0, 0))
            if size > 0 and used >= size * ACP_CONTEXT_FILL:
                self._retire(sid)
            return answer

    def reset(self, convo_key: str = "global") -> None:
        """Forget a conversation — the next ask() on this key starts fresh.

        Used by the 🔄 regenerate control and to clear a stale thread."""
        sid = self._sessions.get(convo_key)
        if sid is not None:
            self._retire(sid)

    def _retire(self, sid: str) -> None:
        """Drop a session (best-effort close) so its key opens a fresh one next."""
        key = self._sid_key.pop(sid, None)
        if key is not None and self._sessions.get(key) == sid:
            self._sessions.pop(key, None)
        self._usage.pop(sid, None)
        self._answer.pop(sid, None)
        self._stream_cbs.pop(sid, None)
        self._tool_cbs.pop(sid, None)
        try:
            self._write({"jsonrpc": "2.0", "method": "session/close",
                         "params": {"sessionId": sid}})
        except Exception:
            pass

    async def close(self) -> None:
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass
