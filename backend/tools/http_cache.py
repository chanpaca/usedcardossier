"""A small on-disk cache for the keyless vehicle APIs.

Every call to fueleconomy.gov and NHTSA goes through here. The cache matters
for three reasons beyond speed:

  * the demo cannot fail because a government API is slow or down;
  * a fresh clone ships with a seeded cache, so the app is useful offline and
    the 10-minute setup rule does not depend on network conditions;
  * token and latency measurements for the report are reproducible, because
    repeated runs hit identical upstream data.

Entries never expire on their own. Vehicle specs for a past model year do not
change, and recall/complaint counts move slowly; `refresh=True` forces a
re-fetch when we deliberately re-seed.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from contextlib import closing
from pathlib import Path
from typing import Any

USER_AGENT = "UsedCarDossier/1.0 (Yale SOM MGT409 course project)"
DEFAULT_TIMEOUT = 25

_SCHEMA = """
CREATE TABLE IF NOT EXISTS responses (
    key        TEXT PRIMARY KEY,
    url        TEXT NOT NULL,
    body       BLOB NOT NULL,
    fetched_at REAL NOT NULL
);
"""

# Bodies are stored zlib-compressed. NHTSA complaint payloads are long,
# repetitive English prose and compress by roughly 10x; uncompressed, the
# seeded cache that ships with the repo was 50 MB, which is an unfriendly
# clone for a grader working to a 10-minute setup budget.
#
# Reads accept either form, so a cache written by an older build still works.
_COMPRESS_LEVEL = 6


def _pack(text: str) -> bytes:
    return zlib.compress(text.encode("utf-8"), _COMPRESS_LEVEL)


def _unpack(blob: Any) -> str:
    if isinstance(blob, str):          # legacy uncompressed row
        return blob
    try:
        return zlib.decompress(blob).decode("utf-8")
    except zlib.error:
        return bytes(blob).decode("utf-8")


class FetchError(RuntimeError):
    """Upstream request failed and no cached copy was available."""


def _cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


class HttpCache:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()
        # Counters let the report quantify how much the cache actually saves.
        self.hits = 0
        self.misses = 0

    # -- storage -------------------------------------------------------------

    def _read(self, key: str) -> str | None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT body FROM responses WHERE key = ?", (key,)
            ).fetchone()
        return _unpack(row[0]) if row else None

    def _write(self, key: str, url: str, body: str) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO responses (key, url, body, fetched_at) "
                "VALUES (?, ?, ?, ?)",
                (key, url, _pack(body), time.time()),
            )
            conn.commit()

    # -- fetching ------------------------------------------------------------

    def get_json(
        self,
        base_url: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        refresh: bool = False,
    ) -> Any:
        """GET a JSON document, serving from cache unless `refresh` is set."""
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        key = _cache_key(url)

        if not refresh:
            cached = self._read(key)
            if cached is not None:
                self.hits += 1
                return json.loads(cached)

        self.misses += 1
        request = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
        )
        try:
            with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
                body = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # Network trouble: a stale cached copy beats failing the request.
            stale = self._read(key)
            if stale is not None:
                return json.loads(stale)
            raise FetchError(f"could not fetch {url}: {exc}") from exc

        # Some fueleconomy endpoints answer with an empty body for an unknown
        # combination. Cache it as null so we do not retry on every request.
        payload = json.loads(body) if body.strip() else None
        self._write(key, url, json.dumps(payload))
        return payload

    # -- introspection -------------------------------------------------------

    def stats(self) -> dict[str, int]:
        with closing(sqlite3.connect(self.db_path)) as conn:
            (entries,) = conn.execute("SELECT COUNT(*) FROM responses").fetchone()
        return {"entries": entries, "hits": self.hits, "misses": self.misses}
