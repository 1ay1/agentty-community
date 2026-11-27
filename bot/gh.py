"""Tiny async GitHub REST client for the community bot.

Conditional requests (ETag) keep polling free: a 304 doesn't count against the
authenticated rate limit. The token comes from GITHUB_TOKEN, else `gh auth token`.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time

import aiohttp

REPO = os.environ.get("GITHUB_REPO", "agentty-org/agentty")
API = "https://api.github.com"


def _token() -> str:
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok or not shutil.which("gh"):
        return tok
    import subprocess
    try:
        return subprocess.run(["gh", "auth", "token"], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


class GitHub:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._etags: dict[str, tuple[str, object]] = {}
        self._cache: dict[str, tuple[float, object]] = {}
        self.token = _token()

    @property
    def can_write(self) -> bool:
        return bool(self.token)

    async def _s(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            h = {"Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "agentty-community-bot"}
            if self.token:
                h["Authorization"] = f"Bearer {self.token}"
            self._session = aiohttp.ClientSession(
                headers=h, timeout=aiohttp.ClientTimeout(total=20))
        return self._session

    async def get(self, path: str, *, poll: bool = False, ttl: float = 0):
        """GET a path. poll=True uses ETags and returns None when unchanged.
        ttl>0 serves a short in-memory cache (for chat-triggered lookups)."""
        if ttl and path in self._cache and time.monotonic() - self._cache[path][0] < ttl:
            return self._cache[path][1]
        s = await self._s()
        headers = {}
        if poll and path in self._etags:
            headers["If-None-Match"] = self._etags[path][0]
        async with s.get(API + path, headers=headers) as r:
            if r.status == 304:
                return None
            if r.status == 404:
                return {}
            r.raise_for_status()
            data = await r.json()
            if poll and r.headers.get("ETag"):
                self._etags[path] = (r.headers["ETag"], data)
            if ttl:
                self._cache[path] = (time.monotonic(), data)
            return data

    async def post(self, path: str, body: dict):
        s = await self._s()
        async with s.post(API + path, json=body) as r:
            r.raise_for_status()
            return await r.json()

    # ── conveniences ────────────────────────────────────────────────────
    async def repo(self, *, poll=False):
        return await self.get(f"/repos/{REPO}", poll=poll, ttl=0 if poll else 120)

    async def latest_release(self, *, poll=False):
        return await self.get(f"/repos/{REPO}/releases/latest", poll=poll,
                              ttl=0 if poll else 120)

    async def releases(self, n: int = 5):
        return await self.get(f"/repos/{REPO}/releases?per_page={n}", ttl=120)

    async def issue(self, number: int):
        """Issue OR pull request (GitHub serves both from /issues)."""
        return await self.get(f"/repos/{REPO}/issues/{number}", ttl=60)

    async def search_issues(self, query: str, n: int = 8):
        from urllib.parse import quote
        q = quote(f"repo:{REPO} {query}")
        res = await self.get(f"/search/issues?q={q}&per_page={n}", ttl=60)
        return (res or {}).get("items", [])

    async def create_issue(self, title: str, body: str, labels: list[str] | None = None):
        payload = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        try:
            return await self.post(f"/repos/{REPO}/issues", payload)
        except aiohttp.ClientResponseError:
            if labels:   # label may not exist — retry without it
                return await self.post(f"/repos/{REPO}/issues",
                                       {"title": title, "body": body})
            raise

    async def download_count(self) -> int:
        total = 0
        for page in range(1, 6):
            rel = await self.get(f"/repos/{REPO}/releases?per_page=100&page={page}", ttl=900)
            if not rel:
                break
            total += sum(a.get("download_count", 0) for r in rel for a in r.get("assets", []))
            if len(rel) < 100:
                break
        return total

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


async def _selftest() -> None:
    g = GitHub()
    print((await g.repo())["stargazers_count"], (await g.latest_release())["tag_name"])
    await g.close()


if __name__ == "__main__":
    asyncio.run(_selftest())
