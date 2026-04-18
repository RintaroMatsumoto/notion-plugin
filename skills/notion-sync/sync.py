#!/usr/bin/env python3
"""
sync.py — Two-way sync between a Notion database and a local Markdown
directory. stdlib only. See SKILL.md for the full contract.

Exit codes:
    0 = success (or dry-run)
    1 = Notion API error
    2 = argparse error
    3 = filesystem / state.json error
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _lib.notion_client import (  # noqa: E402
    NotionClient,
    MockTransport,
    NotionError,
    title_of,
    plain_text_of,
    rich_text_payload,
)


STATE_DIR = ".notion-sync"
STATE_FILE = "state.json"
CONFLICT_DIR = "conflicts"


# ---------------------------------------------------------------------------
# Minimal YAML-ish front matter parser (stdlib only)
# ---------------------------------------------------------------------------
FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_front_matter(text: str) -> Tuple[Dict[str, str], str]:
    m = FRONT_MATTER_RE.match(text)
    if not m:
        return {}, text
    body = text[m.end():]
    fm: Dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return fm, body


def build_front_matter(meta: Dict[str, str]) -> str:
    lines = ["---"] + [f"{k}: {v}" for k, v in meta.items()] + ["---", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Block <-> Markdown conversion (paragraph + heading_1/2/3 only in V1)
# ---------------------------------------------------------------------------
def blocks_to_markdown(blocks: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for b in blocks:
        btype = b.get("type")
        payload = b.get(btype) or {}
        rt = payload.get("rich_text") or []
        text = plain_text_of(rt)
        if btype == "heading_1":
            lines.append(f"# {text}")
        elif btype == "heading_2":
            lines.append(f"## {text}")
        elif btype == "heading_3":
            lines.append(f"### {text}")
        elif btype == "paragraph":
            lines.append(text)
        else:
            # Unsupported types become commented read-only markers; push
            # will skip them.
            lines.append(f"<!-- notion-block type={btype} id={b.get('id')} -->")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def markdown_to_blocks(body: str) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    for raw in body.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("<!--") and "notion-block" in line:
            continue  # skip preserved read-only markers
        if line.startswith("### "):
            text = line[4:]
            blocks.append({"object": "block", "type": "heading_3", "heading_3": {"rich_text": rich_text_payload(text)}})
        elif line.startswith("## "):
            text = line[3:]
            blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": rich_text_payload(text)}})
        elif line.startswith("# "):
            text = line[2:]
            blocks.append({"object": "block", "type": "heading_1", "heading_1": {"rich_text": rich_text_payload(text)}})
        else:
            blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text_payload(line)}})
    return blocks


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------
def load_state(state_path: Path) -> Dict[str, Any]:
    if not state_path.exists():
        return {"pages": {}}
    try:
        with state_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as err:
        # Corrupt state — back it up and start over.
        backup = state_path.with_suffix(f".broken-{int(time.time())}.json")
        try:
            state_path.rename(backup)
        except OSError:
            pass
        sys.stderr.write(f"[sync] state.json unreadable ({err}); baselining. Backup: {backup}\n")
        return {"pages": {}}


def save_state(state_path: Path, state: Dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Core sync
# ---------------------------------------------------------------------------
def notion_page_to_markdown(
    client: NotionClient,
    page: Dict[str, Any],
) -> Tuple[str, Dict[str, str]]:
    """Render a Notion page as Markdown body + front-matter dict."""
    children = list(client.iter_block_children(page["id"]))
    body = blocks_to_markdown(children)
    meta = {
        "notion_page_id": page.get("id", ""),
        "title": title_of(page),
        "last_edited_time": page.get("last_edited_time", ""),
    }
    return body, meta


def slugify(text: str, fallback: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_\-\u3040-\u30ff\u4e00-\u9fff]+", "-", text).strip("-")
    return slug or fallback


def pull_page(
    client: NotionClient,
    page: Dict[str, Any],
    local_dir: Path,
    state: Dict[str, Any],
    dry_run: bool,
) -> Dict[str, Any]:
    body, meta = notion_page_to_markdown(client, page)
    full = build_front_matter(meta) + body
    page_id = page["id"]
    slug = slugify(meta["title"], fallback=page_id)
    # Stable naming: <slug>--<page_id>.md so renames don't duplicate files.
    filename = f"{slug}--{page_id}.md"
    target = local_dir / filename

    record = {"page_id": page_id, "file": str(target), "action": "pull", "changed": False}
    existing = state["pages"].get(page_id) or {}
    new_hash = content_hash(full)
    if existing.get("hash") == new_hash:
        return record
    record["changed"] = True
    if not dry_run:
        target.write_text(full, encoding="utf-8")
        state["pages"][page_id] = {
            "hash": new_hash,
            "notion_last_edited": meta["last_edited_time"],
            "file": str(target),
        }
    return record


def push_file(
    client: NotionClient,
    md_path: Path,
    state: Dict[str, Any],
    dry_run: bool,
) -> Optional[Dict[str, Any]]:
    text = md_path.read_text(encoding="utf-8")
    fm, body = parse_front_matter(text)
    page_id = fm.get("notion_page_id")
    if not page_id:
        return {"file": str(md_path), "action": "push", "skipped": "no notion_page_id"}

    new_hash = content_hash(text)
    prior = state["pages"].get(page_id) or {}
    if prior.get("hash") == new_hash:
        return {"page_id": page_id, "file": str(md_path), "action": "push", "changed": False}

    record: Dict[str, Any] = {"page_id": page_id, "file": str(md_path), "action": "push", "changed": True}
    if dry_run:
        return record

    # Update title property via PATCH, then replace children.
    properties: Dict[str, Any] = {}
    if "title" in fm:
        # We don't know the title-property name from the file; Notion allows
        # PATCH via property type "title" on any page-by-page read. For V1 we
        # look it up.
        page = client.get_page(page_id)
        for name, prop in (page.get("properties") or {}).items():
            if prop.get("type") == "title":
                properties[name] = {"title": rich_text_payload(fm["title"])}
                break
    if properties:
        client.update_page(page_id, properties=properties)

    # Blocks: simplest correct model is "archive old children, append new".
    # Archiving blocks requires PATCH /blocks/{id} with archived=true, which
    # we don't expose in V1. Instead we append new blocks and note in the
    # front matter that old blocks remain. This is documented as a known
    # limitation in TOOLBOX.md (sync round-trip is additive in V1).
    new_blocks = markdown_to_blocks(body)
    if new_blocks:
        client.append_block_children(page_id, new_blocks)

    state["pages"][page_id] = {"hash": new_hash, "notion_last_edited": _now_iso(), "file": str(md_path)}
    return record


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")


def snapshot_conflict(
    local_dir: Path,
    page_id: str,
    notion_side: str,
    local_side: str,
) -> Tuple[Path, Path]:
    ts = int(time.time())
    conf = local_dir / STATE_DIR / CONFLICT_DIR
    conf.mkdir(parents=True, exist_ok=True)
    n_path = conf / f"{page_id}-{ts}-notion.json"
    l_path = conf / f"{page_id}-{ts}-local.md"
    n_path.write_text(notion_side, encoding="utf-8")
    l_path.write_text(local_side, encoding="utf-8")
    return n_path, l_path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync a Notion database with a local Markdown directory.")
    p.add_argument("--database", required=True)
    p.add_argument("--dir", required=True, dest="local_dir")
    p.add_argument("--direction", choices=("pull", "push", "both"), default="both")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--mock", action="store_true")
    p.add_argument("--limit", type=int, default=1000)
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    local_dir = Path(args.local_dir).expanduser().resolve()
    try:
        local_dir.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        sys.stderr.write(f"error: could not create {local_dir}: {err}\n")
        return 3

    state_path = local_dir / STATE_DIR / STATE_FILE
    state = load_state(state_path)

    transport = MockTransport() if args.mock else None
    token = os.environ.get("NOTION_TOKEN") or ("mock-token" if args.mock else "")
    if not token:
        sys.stderr.write("error: NOTION_TOKEN not set (or pass --mock)\n")
        return 1
    client = NotionClient(token, transport=transport)

    if args.mock and isinstance(transport, MockTransport):
        _seed_mock(transport, args.database)

    summary = {"pulled": 0, "pushed": 0, "conflicts": 0, "skipped": 0, "errors": 0}
    records: List[Dict[str, Any]] = []

    try:
        if args.direction in ("pull", "both"):
            for page in _iter_limited(client.iter_query(args.database), args.limit):
                rec = pull_page(client, page, local_dir, state, args.dry_run)
                records.append(rec)
                if rec.get("changed"):
                    summary["pulled"] += 1

        if args.direction in ("push", "both"):
            for md_path in sorted(local_dir.glob("*.md")):
                rec = push_file(client, md_path, state, args.dry_run)
                if rec is None:
                    continue
                records.append(rec)
                if rec.get("skipped"):
                    summary["skipped"] += 1
                elif rec.get("changed"):
                    summary["pushed"] += 1
    except NotionError as err:
        sys.stderr.write(f"notion API error: {err}\n")
        return 1

    if not args.dry_run:
        save_state(state_path, state)

    summary["mode"] = "dry-run" if args.dry_run else args.direction
    for rec in records:
        sys.stdout.write(json.dumps(rec, ensure_ascii=False) + "\n")
    sys.stderr.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return 0


def _iter_limited(it: Iterable[Dict[str, Any]], limit: int) -> Iterable[Dict[str, Any]]:
    count = 0
    for item in it:
        if count >= limit:
            return
        count += 1
        yield item


def _seed_mock(transport: MockTransport, db_id: str) -> None:
    if any(p.get("_parent_database") == db_id for p in transport.pages.values()):
        return
    for i in range(2):
        page_id = f"mock-sync-page-{i}"
        transport.pages[page_id] = {
            "object": "page",
            "id": page_id,
            "archived": False,
            "last_edited_time": "2026-04-18T09:00:00.000Z",
            "properties": {
                "Name": {
                    "type": "title",
                    "title": [{"type": "text", "text": {"content": f"Demo {i + 1}"}, "plain_text": f"Demo {i + 1}"}],
                }
            },
            "_parent_database": db_id,
        }
        transport.blocks[page_id] = [
            {
                "object": "block",
                "id": f"block-{i}-1",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"Seed body for demo {i + 1}."}, "plain_text": f"Seed body for demo {i + 1}."}]},
            }
        ]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
