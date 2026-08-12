#!/usr/bin/env python3
"""ACP brain for the agentty community bot.

Drives a persistent `agentty acp` subprocess over stdio (the same Agent Client
Protocol Zed speaks) and exposes one async coroutine — `AcpBrain.ask(question)`
— that returns agentty's natural-language answer.

Why this is the *smart* backend: agentty is itself an AI coding agent, already
authenticated on this box (OAuth). We don't reimplement intelligence or ship an
API key — we let the real agent answer, grounded in reality, using its own
login. The bot just relays.

Safety: the ACP session runs in the **Ask** profile and this client AUTO-DENIES
every permission request (file writes, bash, network side-effects). A Discord
user therefore can never make agentty edit files or run commands on the host —
it can only read/reason/answer. Each question gets a FRESH session, so users
never see each other's context. Prompts are serialised (one turn at a time)
behind an asyncio.Lock because an ACP turn is stateful.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Optional


AGENTTY_BIN = os.environ.get("AGENTTY_BIN", "agentty")
# Workspace handed to the agent. A read-only-ish scratch dir keeps any stray
# tool call away from real projects; the agent mostly answers from its own
# knowledge + the (denied) tools.
ACP_WORKSPACE = os.environ.get("ACP_WORKSPACE", "/tmp/agentty-bot-ws")
# Per-question wall-clock ceiling.
ACP_TIMEOUT = float(os.environ.get("ACP_TIMEOUT", "90"))

# System framing prepended to every user question so the agent answers as the
# community helper: concise, Discord-friendly, honest about uncertainty.
PREAMBLE = (
    "You are the agentty community assistant answering a question in the "
    "agentty Discord. agentty is a fast, native (C++), single-binary terminal "
    "coding agent with local hybrid RAG, Smart Mode model routing, sandboxing, "
    "ACP/MCP support, and skills. Answer the user's question directly and "
    "concisely for a chat message: at most a few short paragraphs, use compact "
    "markdown, prefer concrete commands/links, and do NOT edit files or run "
    "commands — just explain. If you are unsure, say so and point to the docs "
    "at https://agentty.org/docs. Do not call tools unless truly necessary.\n\n"
    "User question:\n"
)


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
        self._chunks: dict[str, list[str]] = {}
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
                AGENTTY_BIN, "acp", "-w", ACP_WORKSPACE,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self._pending.clear()
            self._chunks.clear()
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
                line = await self._proc.stdout.readline()
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
                self._chunks.setdefault(sid, []).append(content.get("text", ""))

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
    async def ask(self, question: str) -> str:
        """Ask the agent a question; return its answer text (may raise)."""
        question = question.strip()
        if not question:
            return ""
        async with self._lock:
            await self._ensure_started()
            # Fresh session per question — no cross-user context bleed.
            new = await self._request("session/new",
                                      {"cwd": ACP_WORKSPACE, "mcpServers": []})
            sid = new["sessionId"]
            self._chunks[sid] = []
            try:
                await asyncio.wait_for(
                    self._request("session/prompt", {
                        "sessionId": sid,
                        "prompt": [{"type": "text", "text": PREAMBLE + question}],
                    }),
                    timeout=ACP_TIMEOUT,
                )
            except asyncio.TimeoutError:
                # Cancel the turn so the agent stops working on it.
                try:
                    self._write({"jsonrpc": "2.0", "method": "session/cancel",
                                 "params": {"sessionId": sid}})
                except Exception:
                    pass
                text = "".join(self._chunks.pop(sid, []))
                return text or "⏳ That took too long — try a more specific question."
            finally:
                # Best-effort close so sessions don't pile up.
                try:
                    self._write({"jsonrpc": "2.0", "method": "session/close",
                                 "params": {"sessionId": sid}})
                except Exception:
                    pass
            return "".join(self._chunks.pop(sid, [])).strip()

    async def close(self) -> None:
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass
