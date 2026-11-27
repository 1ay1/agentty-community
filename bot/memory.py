"""Community memory: a local full-text index of everything said on the agentty
Discord and everything on GitHub (issues, PRs, commits, releases).

Retrieval runs here, locally and for free (SQLite FTS5, bm25 ranking). The agent
only gets the handful of snippets relevant to the question, so it can "know"
the whole history of the community without paying to read it.
"""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).with_name("memory.db")

_STOP = set("""a an and are as at be been but by can could did do does doing done for from
had has have he her him his how i if in into is it its just me my no not of on or our
she so some than that the their them then there these they this those to too up us was
we were what when where which who why will with would you your yours about also any get
got like make more most much need only out over please should still thing think want way
right wrong true correct really actually agentty bot yes yeah ok okay hey hi thanks""".split())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Memory:
    def __init__(self, path: Path = DB_PATH) -> None:
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS doc (
                key TEXT PRIMARY KEY,      -- d:<msgid> | gh:<n> | c:<sha> | r:<tag>
                kind TEXT, place TEXT, author TEXT, ts TEXT, url TEXT, text TEXT);
            CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5(
                text, author, place, content='doc', content_rowid='rowid',
                tokenize='porter unicode61');
            CREATE TRIGGER IF NOT EXISTS doc_ai AFTER INSERT ON doc BEGIN
                INSERT INTO doc_fts(rowid, text, author, place)
                VALUES (new.rowid, new.text, new.author, new.place); END;
            CREATE TRIGGER IF NOT EXISTS doc_ad AFTER DELETE ON doc BEGIN
                INSERT INTO doc_fts(doc_fts, rowid, text, author, place)
                VALUES ('delete', old.rowid, old.text, old.author, old.place); END;
            CREATE TRIGGER IF NOT EXISTS doc_au AFTER UPDATE ON doc BEGIN
                INSERT INTO doc_fts(doc_fts, rowid, text, author, place)
                VALUES ('delete', old.rowid, old.text, old.author, old.place);
                INSERT INTO doc_fts(rowid, text, author, place)
                VALUES (new.rowid, new.text, new.author, new.place); END;
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
        """)

    # ── writes ──────────────────────────────────────────────────────────
    def put(self, key: str, kind: str, place: str, author: str, ts: str, url: str,
            text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.db.execute(
            "INSERT INTO doc(key,kind,place,author,ts,url,text) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET text=excluded.text, place=excluded.place, "
            "author=excluded.author, url=excluded.url",
            (key, kind, place, author, ts, url, text[:4000]))

    def commit(self) -> None:
        self.db.commit()

    def delete(self, key: str) -> None:
        self.db.execute("DELETE FROM doc WHERE key=?", (key,))

    def meta(self, k: str, v: str | None = None) -> str | None:
        if v is None:
            row = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
            return row[0] if row else None
        self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, v))
        self.db.commit()
        return v

    def count(self) -> dict[str, int]:
        return dict(self.db.execute("SELECT kind, count(*) FROM doc GROUP BY kind"))

    # ── reads ───────────────────────────────────────────────────────────
    @staticmethod
    def terms(text: str, cap: int = 14) -> list[str]:
        words = re.findall(r"[A-Za-z][A-Za-z0-9_+.-]{2,}", text or "")
        out: list[str] = []
        for w in words:
            w = w.strip(".-").lower()
            if len(w) > 2 and w not in _STOP and w not in out:
                out.append(w)
        return out[:cap]

    def search(self, text: str, k: int = 6, exclude: set[str] | None = None,
               budget: int = 2600) -> list[dict]:
        terms = self.terms(text)
        if not terms:
            return []
        q = " OR ".join('"' + t.replace('"', "") + '"' for t in terms)
        try:
            rows = self.db.execute(
                "SELECT d.key,d.kind,d.place,d.author,d.ts,d.url,d.text, bm25(doc_fts, 1.0, 0.6, 0.3) AS s "
                "FROM doc_fts JOIN doc d ON d.rowid = doc_fts.rowid "
                "WHERE doc_fts MATCH ? ORDER BY s LIMIT 40", (q,)).fetchall()
        except sqlite3.OperationalError:
            return []
        now = time.time()
        scored = []
        for key, kind, place, author, ts, url, txt, s in rows:
            if exclude and key in exclude:
                continue
            try:
                age_days = (now - datetime.fromisoformat(ts).timestamp()) / 86400
            except Exception:
                age_days = 365
            # bm25 is negative (lower = better); nudge toward recent + GitHub facts
            bonus = 1.0 + 0.6 / (1 + age_days / 14) + (0.15 if kind in ("issue", "pr", "release") else 0)
            scored.append((s * bonus, dict(key=key, kind=kind, place=place, author=author,
                                           ts=ts, url=url, text=txt)))
        scored.sort(key=lambda x: x[0])
        out, used = [], 0
        for _, d in scored:
            snippet = _snip(d["text"], terms, 420)
            if used + len(snippet) > budget:
                break
            d["snippet"] = snippet
            out.append(d)
            used += len(snippet) + 80
            if len(out) >= k:
                break
        return out


def _snip(text: str, terms: list[str], n: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= n:
        return text
    low = text.lower()
    pos = min((p for p in (low.find(t) for t in terms) if p >= 0), default=0)
    start = max(0, pos - n // 3)
    return ("…" if start else "") + text[start:start + n].strip() + "…"


def render_hits(hits: list[dict]) -> str:
    lines = []
    for h in hits:
        date = (h["ts"] or "")[:10]
        tag = {"discord": f"#{h['place']}", "issue": h["place"], "pr": h["place"],
               "commit": "commit", "release": "release"}.get(h["kind"], h["kind"])
        lines.append(f"- [{tag} · {h['author']} · {date}] {h['snippet']} <{h['url']}>")
    return "\n".join(lines)
