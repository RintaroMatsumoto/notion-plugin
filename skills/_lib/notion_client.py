"""
notion_client.py — Shared Notion REST API v1 client for notion-plugin skills.

Design goals:
- stdlib only (urllib); no pip dependency.
- Token-bucket rate limiter (3 rps) with 429 Retry-After honoring.
- Exponential backoff for 5xx (base 500ms, max 5 retries).
- Cursor-based pagination exposed as generators (never load full lists).
- Injectable transport for mock testing without a real Notion token.

See TOOLBOX.md sections 1-3 for the rationale behind these choices.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
DEFAULT_TIMEOUT = 30.0
DEFAULT_PAGE_SIZE = 100
MAX_RETRIES_5XX = 5
BACKOFF_BASE_SECONDS = 0.5


class NotionError(RuntimeError):
    """Raised when the Notion API returns a non-recoverable error."""

    def __init__(self, status: int, body: Any, url: str) -> None:
        self.status = status
        self.body = body
        self.url = url
        super().__init__(f"Notion API {status} at {url}: {body}")


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
class _TokenBucket:
    """Simple token bucket — capacity tokens, refilled at rate tokens/sec.

    Notion's documented limit is 'an average of 3 requests per second'
    (see https://developers.notion.com/reference/request-limits). A
    capacity of 3 with a refill of 3/sec hugs that envelope without
    starving short bursts.
    """

    def __init__(self, rate_per_second: float = 3.0, capacity: float = 3.0) -> None:
        self.rate = float(rate_per_second)
        self.capacity = float(capacity)
        self._tokens = float(capacity)
        self._last = time.monotonic()

    def acquire(self) -> None:
        while True:
            now = time.monotonic()
            elapsed = now - self._last
            self._last = now
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            # not enough budget yet — sleep just long enough to earn 1 token
            deficit = 1.0 - self._tokens
            time.sleep(deficit / self.rate)


# ---------------------------------------------------------------------------
# Transport abstraction
# ---------------------------------------------------------------------------
class _UrllibTransport:
    """Real HTTP transport using urllib (stdlib)."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Dict[str, str],
        body: Optional[bytes],
        timeout: float,
    ) -> Tuple[int, Dict[str, str], bytes]:
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as err:
            data = err.read() if err.fp else b""
            return err.code, dict(err.headers or {}), data


class MockTransport:
    """In-memory Notion mock — just enough surface to exercise every skill.

    Stores databases, pages, and block children in dicts keyed by id.
    Responses mimic the shape of the real Notion API for the endpoints
    this plugin actually calls. Unknown endpoints raise so tests fail
    loudly instead of passing on a silent no-op.

    Construct with an optional ``seed`` dict of the form::

        {
            "databases": {db_id: {...database object...}},
            "pages": {page_id: {...page object...}},
            "blocks": {parent_id: [child_block, ...]},
        }
    """

    def __init__(self, seed: Optional[Dict[str, Any]] = None) -> None:
        seed = seed or {}
        self.databases: Dict[str, Dict[str, Any]] = dict(seed.get("databases") or {})
        self.pages: Dict[str, Dict[str, Any]] = dict(seed.get("pages") or {})
        self.blocks: Dict[str, List[Dict[str, Any]]] = dict(seed.get("blocks") or {})
        self.calls: List[Tuple[str, str, Optional[Dict[str, Any]]]] = []
        self._id_counter = 0

    # --- helpers ---------------------------------------------------------
    def _next_id(self, prefix: str) -> str:
        self._id_counter += 1
        return f"{prefix}-mock-{self._id_counter:04d}"

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

    def _pages_in_database(self, db_id: str) -> List[Dict[str, Any]]:
        return [p for p in self.pages.values() if p.get("_parent_database") == db_id]

    # --- main entrypoint -------------------------------------------------
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Dict[str, str],
        body: Optional[bytes],
        timeout: float,
    ) -> Tuple[int, Dict[str, str], bytes]:
        parsed = urllib.parse.urlparse(url)
        path = parsed.path
        payload = json.loads(body.decode("utf-8")) if body else None
        self.calls.append((method, path, payload))

        # --- Databases ---
        if method == "POST" and path == "/v1/databases":
            db_id = self._next_id("db")
            obj = {
                "object": "database",
                "id": db_id,
                "created_time": self._now(),
                "last_edited_time": self._now(),
                "title": payload.get("title", []),
                "properties": payload.get("properties", {}),
                "parent": payload.get("parent", {}),
            }
            self.databases[db_id] = obj
            return 200, {"Content-Type": "application/json"}, json.dumps(obj).encode()

        if method == "GET" and path.startswith("/v1/databases/"):
            db_id = path.split("/", 3)[3]
            obj = self.databases.get(db_id)
            if not obj:
                return self._not_found(url)
            return 200, {}, json.dumps(obj).encode()

        if method == "POST" and path.endswith("/query") and path.startswith("/v1/databases/"):
            db_id = path.split("/")[3]
            # pagination: we don't honor filters in mock, skills decide relevance
            page_size = int((payload or {}).get("page_size") or DEFAULT_PAGE_SIZE)
            start_cursor = (payload or {}).get("start_cursor")
            all_pages = self._pages_in_database(db_id)
            start = 0
            if start_cursor:
                try:
                    start = int(start_cursor)
                except ValueError:
                    start = 0
            chunk = all_pages[start : start + page_size]
            has_more = start + page_size < len(all_pages)
            result = {
                "object": "list",
                "results": chunk,
                "has_more": has_more,
                "next_cursor": str(start + page_size) if has_more else None,
            }
            return 200, {}, json.dumps(result).encode()

        # --- Pages ---
        if method == "POST" and path == "/v1/pages":
            page_id = self._next_id("page")
            parent = payload.get("parent", {})
            obj = {
                "object": "page",
                "id": page_id,
                "created_time": self._now(),
                "last_edited_time": self._now(),
                "archived": False,
                "properties": payload.get("properties", {}),
                "parent": parent,
            }
            if parent.get("database_id"):
                obj["_parent_database"] = parent["database_id"]
            self.pages[page_id] = obj
            # children blocks
            for child in payload.get("children", []) or []:
                self._append_block(page_id, child)
            return 200, {}, json.dumps(obj).encode()

        if method == "GET" and path.startswith("/v1/pages/"):
            page_id = path.split("/", 3)[3]
            obj = self.pages.get(page_id)
            if not obj:
                return self._not_found(url)
            return 200, {}, json.dumps(obj).encode()

        if method == "PATCH" and path.startswith("/v1/pages/"):
            page_id = path.split("/", 3)[3]
            obj = self.pages.get(page_id)
            if not obj:
                return self._not_found(url)
            if "properties" in payload:
                obj["properties"].update(payload["properties"])
            if "archived" in payload:
                obj["archived"] = payload["archived"]
            obj["last_edited_time"] = self._now()
            return 200, {}, json.dumps(obj).encode()

        # --- Blocks ---
        if method == "GET" and path.startswith("/v1/blocks/") and path.endswith("/children"):
            parent_id = path.split("/")[3]
            children = self.blocks.get(parent_id, [])
            result = {
                "object": "list",
                "results": children,
                "has_more": False,
                "next_cursor": None,
            }
            return 200, {}, json.dumps(result).encode()

        if method == "PATCH" and path.startswith("/v1/blocks/") and path.endswith("/children"):
            parent_id = path.split("/")[3]
            for child in (payload or {}).get("children", []):
                self._append_block(parent_id, child)
            result = {"object": "list", "results": self.blocks.get(parent_id, [])}
            return 200, {}, json.dumps(result).encode()

        # --- Fallback ---
        return self._not_found(url)

    def _append_block(self, parent_id: str, block_payload: Dict[str, Any]) -> None:
        block_id = self._next_id("block")
        block = dict(block_payload)
        block.update({"object": "block", "id": block_id, "has_children": False})
        self.blocks.setdefault(parent_id, []).append(block)

    def _not_found(self, url: str) -> Tuple[int, Dict[str, str], bytes]:
        body = json.dumps({"object": "error", "status": 404, "code": "object_not_found", "message": url}).encode()
        return 404, {}, body


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class NotionClient:
    """Thin wrapper over Notion REST v1.

    Parameters
    ----------
    token : str
        Notion integration secret (``ntn_...``). Passed as ``Authorization: Bearer``.
    transport : object, optional
        Anything exposing ``request(method, url, *, headers, body, timeout)``.
        Defaults to the urllib transport. Swap in ``MockTransport()`` for
        offline testing.
    notion_version : str, optional
        ``Notion-Version`` header value. Defaults to ``2022-06-28``.
    base_url : str, optional
        API base URL. Override for staging.
    rate_per_second : float, optional
        Token-bucket refill rate. Defaults to 3.0 per Notion docs.
    """

    def __init__(
        self,
        token: str,
        *,
        transport: Any = None,
        notion_version: str = NOTION_VERSION,
        base_url: str = NOTION_API_BASE,
        rate_per_second: float = 3.0,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not token:
            raise ValueError("NotionClient requires a non-empty token (set NOTION_TOKEN or use MockTransport).")
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.notion_version = notion_version
        self.timeout = timeout
        self.transport = transport or _UrllibTransport()
        self._bucket = _TokenBucket(rate_per_second=rate_per_second, capacity=rate_per_second)

    # --- factory ---------------------------------------------------------
    @classmethod
    def from_env(cls, **kwargs: Any) -> "NotionClient":
        token = os.environ.get("NOTION_TOKEN", "")
        if not token:
            raise RuntimeError("NOTION_TOKEN environment variable is not set.")
        return cls(token, **kwargs)

    # --- low-level -------------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.notion_version,
            "Content-Type": "application/json",
            "User-Agent": "notion-plugin/0.1 (+https://github.com/RintaroMatsumoto/notion-plugin)",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        query: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = self.base_url + path
        if query:
            clean = {k: v for k, v in query.items() if v is not None}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean)}"
        payload = json.dumps(body).encode("utf-8") if body is not None else None

        for attempt in range(MAX_RETRIES_5XX + 1):
            self._bucket.acquire()
            status, headers, data = self.transport.request(
                method,
                url,
                headers=self._headers(),
                body=payload,
                timeout=self.timeout,
            )

            # success
            if 200 <= status < 300:
                if not data:
                    return {}
                try:
                    return json.loads(data.decode("utf-8"))
                except json.JSONDecodeError as err:
                    raise NotionError(status, f"invalid JSON: {err}", url) from err

            # rate limit — honor Retry-After
            if status == 429:
                retry_after = _parse_retry_after(headers)
                sys.stderr.write(f"[notion] 429 rate-limited, sleeping {retry_after:.1f}s\n")
                time.sleep(retry_after)
                continue

            # transient server error — exponential backoff
            if 500 <= status < 600 and attempt < MAX_RETRIES_5XX:
                delay = BACKOFF_BASE_SECONDS * (2 ** attempt)
                sys.stderr.write(f"[notion] {status} server error, retry {attempt + 1}/{MAX_RETRIES_5XX} in {delay:.1f}s\n")
                time.sleep(delay)
                continue

            # terminal error
            try:
                parsed = json.loads(data.decode("utf-8")) if data else {}
            except json.JSONDecodeError:
                parsed = data.decode("utf-8", errors="replace")
            raise NotionError(status, parsed, url)

        raise NotionError(0, "retry budget exhausted", url)

    # --- high-level ------------------------------------------------------
    def create_database(
        self,
        parent_page_id: str,
        title: str,
        properties: Dict[str, Any],
    ) -> Dict[str, Any]:
        body = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": properties,
        }
        return self.request("POST", "/databases", body=body)

    def get_database(self, db_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/databases/{db_id}")

    def iter_query(
        self,
        db_id: str,
        *,
        filter_: Optional[Dict[str, Any]] = None,
        sorts: Optional[List[Dict[str, Any]]] = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> Iterator[Dict[str, Any]]:
        cursor: Optional[str] = None
        while True:
            body: Dict[str, Any] = {"page_size": min(page_size, DEFAULT_PAGE_SIZE)}
            if filter_:
                body["filter"] = filter_
            if sorts:
                body["sorts"] = sorts
            if cursor:
                body["start_cursor"] = cursor
            resp = self.request("POST", f"/databases/{db_id}/query", body=body)
            for result in resp.get("results", []):
                yield result
            if not resp.get("has_more"):
                return
            cursor = resp.get("next_cursor")
            if not cursor:
                return

    def create_page(
        self,
        *,
        parent_database_id: Optional[str] = None,
        parent_page_id: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
        children: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if not (parent_database_id or parent_page_id):
            raise ValueError("create_page requires parent_database_id or parent_page_id")
        if parent_database_id:
            parent = {"type": "database_id", "database_id": parent_database_id}
        else:
            parent = {"type": "page_id", "page_id": parent_page_id}
        body: Dict[str, Any] = {"parent": parent, "properties": properties or {}}
        if children:
            body["children"] = children
        return self.request("POST", "/pages", body=body)

    def get_page(self, page_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/pages/{page_id}")

    def update_page(
        self,
        page_id: str,
        *,
        properties: Optional[Dict[str, Any]] = None,
        archived: Optional[bool] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        if properties is not None:
            body["properties"] = properties
        if archived is not None:
            body["archived"] = archived
        if not body:
            raise ValueError("update_page requires properties or archived")
        return self.request("PATCH", f"/pages/{page_id}", body=body)

    def iter_block_children(self, block_id: str, page_size: int = DEFAULT_PAGE_SIZE) -> Iterator[Dict[str, Any]]:
        cursor: Optional[str] = None
        while True:
            query: Dict[str, Any] = {"page_size": min(page_size, DEFAULT_PAGE_SIZE)}
            if cursor:
                query["start_cursor"] = cursor
            resp = self.request("GET", f"/blocks/{block_id}/children", query=query)
            for block in resp.get("results", []):
                yield block
            if not resp.get("has_more"):
                return
            cursor = resp.get("next_cursor")
            if not cursor:
                return

    def append_block_children(
        self,
        block_id: str,
        children: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        # Notion caps children per call at 100 — chunk
        BATCH = 100
        last: Dict[str, Any] = {}
        for i in range(0, len(children), BATCH):
            chunk = children[i : i + BATCH]
            last = self.request("PATCH", f"/blocks/{block_id}/children", body={"children": chunk})
        return last


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_retry_after(headers: Dict[str, str]) -> float:
    """Parse the Retry-After header (seconds or HTTP-date).

    Notion returns seconds in practice, but the RFC allows HTTP-date too.
    Fall back to 1.0s if anything is unparseable.
    """
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if not raw:
        return 1.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 1.0


def plain_text_of(rich_text: List[Dict[str, Any]]) -> str:
    """Flatten a Notion rich_text array into a plain string."""
    return "".join(item.get("plain_text") or item.get("text", {}).get("content", "") for item in rich_text or [])


def title_of(page: Dict[str, Any]) -> str:
    """Return the page's title as plain text, or empty string if missing."""
    props = page.get("properties") or {}
    for prop in props.values():
        if prop.get("type") == "title":
            return plain_text_of(prop.get("title") or [])
    return ""


def rich_text_payload(content: str) -> List[Dict[str, Any]]:
    """Build a rich_text property value from a plain string.

    Splits into <=2000-char chunks (Notion's per-element cap) up to 100
    elements (Notion's per-property cap). See TOOLBOX.md §4.1.
    """
    if not content:
        return []
    CHUNK = 2000
    MAX_ELEMS = 100
    chunks = [content[i : i + CHUNK] for i in range(0, len(content), CHUNK)][:MAX_ELEMS]
    return [{"type": "text", "text": {"content": c}} for c in chunks]
