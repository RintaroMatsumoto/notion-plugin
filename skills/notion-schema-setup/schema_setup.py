#!/usr/bin/env python3
"""
schema_setup.py — Create a Notion database from a brief + column list.

Inference is keyword-based today (see SKILL.md and TOOLBOX.md §5.1);
a future version may hand the brief to Claude for richer type guessing.
stdlib only. Prints JSON to stdout.

Exit codes:
    0 = success (or dry-run completed)
    1 = API failure
    2 = argparse error (default)
    3 = inference failure (no title column could be produced)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Allow importing the shared _lib package when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _lib.notion_client import NotionClient, MockTransport, NotionError  # noqa: E402


# --- keyword → Notion type map ------------------------------------------------
# Order matters: more specific strings first, fallback last.
KEYWORD_MAP: List[Tuple[Tuple[str, ...], str]] = [
    (("title", "name", "タイトル", "見出し", "題名"), "title"),
    (("status", "state", "ステータス", "状態"), "status"),
    (("tag", "tags", "labels", "label", "タグ"), "multi_select"),
    (("priority", "優先度"), "select"),
    (("date", "due", "deadline", "締切", "期限", "日付", "日"), "date"),
    (("url", "link", "リンク"), "url"),
    (("owner", "assignee", "担当", "担当者"), "people"),
    (("note", "notes", "description", "memo", "メモ", "説明", "備考"), "rich_text"),
    (("done", "完了", "チェック"), "checkbox"),
    (("count", "number", "rating", "score", "数", "評価"), "number"),
]
FALLBACK_TYPE = "rich_text"


def infer_type(column_name: str) -> str:
    needle = column_name.strip().lower()
    for keywords, ntype in KEYWORD_MAP:
        for kw in keywords:
            if kw in needle:
                return ntype
    return FALLBACK_TYPE


def property_payload(ntype: str) -> Dict[str, Any]:
    """Build the Notion create-database property definition for a given type."""
    if ntype == "select":
        return {"select": {"options": []}}
    if ntype == "multi_select":
        return {"multi_select": {"options": []}}
    if ntype == "status":
        # Notion auto-provisions default options when you omit 'options'.
        return {"status": {}}
    if ntype == "number":
        return {"number": {"format": "number"}}
    if ntype == "title":
        return {"title": {}}
    if ntype == "rich_text":
        return {"rich_text": {}}
    if ntype == "date":
        return {"date": {}}
    if ntype == "url":
        return {"url": {}}
    if ntype == "people":
        return {"people": {}}
    if ntype == "checkbox":
        return {"checkbox": {}}
    # Safety fallback.
    return {"rich_text": {}}


def build_schema(columns: List[str]) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, str]]]:
    """Return (properties_payload, human_readable_preview).

    Guarantees exactly one title property. The first column whose inferred
    type is `title` (or the literal first column if none match) is
    promoted; further title-like columns are demoted to rich_text.
    """
    preview: List[Dict[str, str]] = []
    inferred: List[Tuple[str, str]] = [(c, infer_type(c)) for c in columns]

    # Pick the title column.
    title_idx = next((i for i, (_, t) in enumerate(inferred) if t == "title"), None)
    if title_idx is None:
        # No column matched 'title'-like keywords — promote the first column.
        if not inferred:
            raise ValueError("At least one column is required.")
        first_name, _ = inferred[0]
        inferred[0] = (first_name, "title")
        title_idx = 0

    # Demote extra titles.
    for i, (name, t) in enumerate(inferred):
        if t == "title" and i != title_idx:
            inferred[i] = (name, "rich_text")

    properties: Dict[str, Dict[str, Any]] = {}
    seen: Dict[str, int] = {}
    for name, t in inferred:
        # Dedup column names (Notion requires unique keys).
        key = name.strip() or "Untitled"
        if key in seen:
            seen[key] += 1
            key = f"{key} ({seen[key]})"
        else:
            seen[key] = 1
        properties[key] = property_payload(t)
        preview.append({"name": key, "type": t})

    return properties, preview


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create a Notion database from a brief + columns.")
    p.add_argument("--parent-page", help="Notion page id that will own the new database.")
    p.add_argument("--title", required=True, help="Human-readable database title.")
    p.add_argument("--columns", required=True, help="Comma-separated column names.")
    p.add_argument("--brief", default="", help="Optional free-text description (reserved).")
    p.add_argument("--dry-run", action="store_true", help="Print schema only; do not create.")
    p.add_argument("--mock", action="store_true", help="Use MockTransport (no real API call).")
    p.add_argument("--output", default="-", help="Write JSON result to this file, or '-' for stdout.")
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    columns = [c.strip() for c in args.columns.split(",") if c.strip()]
    if not columns:
        sys.stderr.write("error: --columns produced no non-empty values\n")
        return 3

    try:
        properties, preview = build_schema(columns)
    except ValueError as err:
        sys.stderr.write(f"error: {err}\n")
        return 3

    result: Dict[str, Any] = {
        "title": args.title,
        "parent_page": args.parent_page,
        "schema_preview": preview,
        "dry_run": args.dry_run,
        "mock": args.mock,
    }

    if args.dry_run and not args.mock:
        result["status"] = "dry-run"
        _write(result, args.output)
        return 0

    # Need a parent page when actually creating (real or mock).
    parent_page = args.parent_page or "mock-parent-page"
    transport = MockTransport() if args.mock else None
    token = os.environ.get("NOTION_TOKEN") or ("mock-token" if args.mock else "")
    if not token:
        sys.stderr.write("error: NOTION_TOKEN not set (or pass --mock).\n")
        return 1

    client = NotionClient(token, transport=transport)
    try:
        created = client.create_database(parent_page, args.title, properties)
    except NotionError as err:
        sys.stderr.write(f"notion API error: {err}\n")
        return 1

    result["status"] = "created"
    result["database_id"] = created.get("id")
    result["url"] = created.get("url")
    _write(result, args.output)
    return 0


def _write(payload: Dict[str, Any], target: str) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if target == "-" or not target:
        sys.stdout.write(text + "\n")
    else:
        Path(target).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
