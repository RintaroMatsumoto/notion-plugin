#!/usr/bin/env python3
"""
cross_reference.py — Propose and optionally write relation links between
pages in a single Notion database, using a stdlib-only Jaccard score.

Exit codes:
    0 = success
    1 = API failure
    2 = argparse
    3 = schema error (relation property missing)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _lib.notion_client import (  # noqa: E402
    NotionClient,
    MockTransport,
    NotionError,
    title_of,
)


TOKEN_RE = re.compile(r"[A-Za-z0-9\u3040-\u30ff\u4e00-\u9fff]+")


def tokens_of(page: Dict[str, Any], tag_props: Optional[List[str]]) -> Set[str]:
    bag: Set[str] = set()
    title = title_of(page).lower()
    bag.update(TOKEN_RE.findall(title))
    props = page.get("properties") or {}
    for name, prop in props.items():
        if tag_props is not None and name not in tag_props:
            continue
        if prop.get("type") == "multi_select":
            for opt in prop.get("multi_select") or []:
                n = (opt.get("name") or "").lower().strip()
                if n:
                    bag.add(n)
    return bag


def jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def find_relation_property(database: Dict[str, Any], name: str) -> None:
    props = database.get("properties") or {}
    if name not in props:
        raise RuntimeError(f"relation property '{name}' not found on database")
    if props[name].get("type") != "relation":
        raise RuntimeError(f"property '{name}' is not of type relation")


def default_tag_props(database: Dict[str, Any]) -> List[str]:
    return [n for n, p in (database.get("properties") or {}).items() if p.get("type") == "multi_select"]


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Auto-link related pages in a Notion database.")
    p.add_argument("--database", required=True)
    p.add_argument("--relation-property", required=True)
    p.add_argument("--threshold", type=float, default=0.3)
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--tag-properties", default=None,
                   help="Comma-separated multi_select property names. Default: all multi_select props.")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--mock", action="store_true")
    p.add_argument("--limit", type=int, default=1000)
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    transport = MockTransport() if args.mock else None
    token = os.environ.get("NOTION_TOKEN") or ("mock-token" if args.mock else "")
    if not token:
        sys.stderr.write("error: NOTION_TOKEN not set (or pass --mock)\n")
        return 1
    client = NotionClient(token, transport=transport)

    if args.mock and isinstance(transport, MockTransport):
        _seed_mock(transport, args.database, args.relation_property)

    try:
        db = client.get_database(args.database)
    except NotionError as err:
        sys.stderr.write(f"notion API error: {err}\n")
        return 1

    try:
        find_relation_property(db, args.relation_property)
    except RuntimeError as err:
        sys.stderr.write(f"error: {err}\n")
        return 3

    tag_props: Optional[List[str]] = (
        [t.strip() for t in args.tag_properties.split(",") if t.strip()]
        if args.tag_properties
        else default_tag_props(db)
    )

    pages = list(_iter_limited(client.iter_query(args.database), args.limit))
    token_map: Dict[str, Set[str]] = {p["id"]: tokens_of(p, tag_props) for p in pages}

    summary = {"pages": len(pages), "proposed": 0, "applied": 0, "errors": 0, "mode": "apply" if args.apply else "dry-run"}

    for page in pages:
        page_id = page["id"]
        scored: List[Tuple[str, float, str]] = []
        for other in pages:
            if other["id"] == page_id:
                continue
            score = jaccard(token_map[page_id], token_map[other["id"]])
            if score >= args.threshold:
                scored.append((other["id"], score, title_of(other)))
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[: args.top_k]
        record: Dict[str, Any] = {
            "page_id": page_id,
            "title": title_of(page),
            "proposed": [{"page_id": i, "title": t, "score": round(s, 3)} for i, s, t in top],
            "applied": False,
        }
        summary["proposed"] += len(top)

        if top and args.apply:
            try:
                client.update_page(
                    page_id,
                    properties={args.relation_property: {"relation": [{"id": i} for i, _, _ in top]}},
                )
                record["applied"] = True
                summary["applied"] += 1
            except NotionError as err:
                record["error"] = str(err)
                summary["errors"] += 1
        sys.stdout.write(json.dumps(record, ensure_ascii=False) + "\n")

    sys.stderr.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return 0 if summary["errors"] == 0 else 1


def _iter_limited(it: Iterable[Dict[str, Any]], limit: int) -> Iterable[Dict[str, Any]]:
    count = 0
    for item in it:
        if count >= limit:
            return
        count += 1
        yield item


def _seed_mock(transport: MockTransport, db_id: str, relation_prop: str) -> None:
    transport.databases[db_id] = {
        "object": "database",
        "id": db_id,
        "properties": {
            "Name": {"type": "title", "title": {}},
            "Tags": {"type": "multi_select", "multi_select": {"options": []}},
            relation_prop: {"type": "relation", "relation": {"database_id": db_id}},
        },
    }
    samples = [
        ("Diffusion models for video", ["diffusion", "video"]),
        ("Video diffusion survey", ["diffusion", "video", "survey"]),
        ("Speculative decoding", ["llm", "inference"]),
        ("Fast LLM inference", ["llm", "inference", "speculative"]),
        ("Unrelated gardening notes", ["tomato"]),
    ]
    for i, (name, tags) in enumerate(samples):
        page_id = f"mock-cr-{i}"
        transport.pages[page_id] = {
            "object": "page",
            "id": page_id,
            "archived": False,
            "last_edited_time": "2026-04-18T00:00:00.000Z",
            "properties": {
                "Name": {"type": "title", "title": [{"type": "text", "text": {"content": name}, "plain_text": name}]},
                "Tags": {"type": "multi_select", "multi_select": [{"name": t} for t in tags]},
                relation_prop: {"type": "relation", "relation": []},
            },
            "_parent_database": db_id,
        }


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
