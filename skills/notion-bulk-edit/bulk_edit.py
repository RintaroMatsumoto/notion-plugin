#!/usr/bin/env python3
"""
bulk_edit.py — Apply a transformation plan to every page in a database.

Dry-run is the default mode. Pass --apply to actually PATCH pages.
stdlib only. Emits JSONL to stdout (or --output) — one line per page.

Exit codes:
    0 = success (dry-run or applied)
    1 = Notion API error
    2 = argparse error
    3 = invalid plan / filter file
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _lib.notion_client import (  # noqa: E402
    NotionClient,
    MockTransport,
    NotionError,
    title_of,
)


def load_json(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as err:
        raise RuntimeError(f"could not read JSON from {path}: {err}") from err


def compute_changes(page: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    """Return the delta to apply to this page.

    Returns a dict with keys ``properties`` (dict to send in PATCH body),
    ``archived`` (bool or None), ``diff`` (list of human-readable strings),
    and ``warnings`` (list).
    """
    current_props = page.get("properties") or {}
    new_props: Dict[str, Any] = {}
    diff: List[str] = []
    warnings: List[str] = []

    # --- set_properties -------------------------------------------------
    for prop_name, value in (plan.get("set_properties") or {}).items():
        if prop_name not in current_props:
            warnings.append(f"unknown property: {prop_name}")
            continue
        new_props[prop_name] = value
        diff.append(f"set {prop_name} = {json.dumps(value, ensure_ascii=False)}")

    # --- append_multi_select -------------------------------------------
    for prop_name, options in (plan.get("append_multi_select") or {}).items():
        if prop_name not in current_props:
            warnings.append(f"unknown property: {prop_name}")
            continue
        cur = current_props[prop_name] or {}
        if cur.get("type") and cur["type"] != "multi_select":
            warnings.append(f"{prop_name} is not multi_select")
            continue
        existing = cur.get("multi_select") or []
        existing_names = {o.get("name") for o in existing}
        merged = list(existing)
        added: List[str] = []
        for opt in options:
            if opt not in existing_names:
                merged.append({"name": opt})
                added.append(opt)
                existing_names.add(opt)
        if added:
            new_props[prop_name] = {"multi_select": merged}
            diff.append(f"append {prop_name}: {added}")

    # --- archive --------------------------------------------------------
    archived: Optional[bool] = None
    if "archive" in plan:
        target = bool(plan["archive"])
        if bool(page.get("archived")) != target:
            archived = target
            diff.append(f"archived -> {target}")

    return {
        "properties": new_props,
        "archived": archived,
        "diff": diff,
        "warnings": warnings,
    }


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bulk-edit pages in a Notion database.")
    p.add_argument("--database", required=True, help="Database ID to operate on.")
    p.add_argument("--plan", required=True, help="Path to plan JSON.")
    p.add_argument("--filter", default=None, help="Path to filter JSON (optional).")
    p.add_argument("--apply", action="store_true", help="Actually PATCH pages. Without this, dry-run.")
    p.add_argument("--mock", action="store_true", help="Use MockTransport (no network).")
    p.add_argument("--limit", type=int, default=1000, help="Max pages to touch. Default 1000.")
    p.add_argument("--output", default="-", help="Write JSONL log to this path or '-' for stdout.")
    return p.parse_args(argv)


def open_output(target: str):
    if target == "-" or not target:
        return sys.stdout, False
    return open(target, "w", encoding="utf-8"), True


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    try:
        plan = load_json(args.plan)
        filter_obj = load_json(args.filter) if args.filter else None
    except RuntimeError as err:
        sys.stderr.write(f"error: {err}\n")
        return 3

    if not isinstance(plan, dict):
        sys.stderr.write("error: plan must be a JSON object\n")
        return 3

    # Build client.
    transport = MockTransport() if args.mock else None
    token = os.environ.get("NOTION_TOKEN") or ("mock-token" if args.mock else "")
    if not token:
        sys.stderr.write("error: NOTION_TOKEN not set (or pass --mock)\n")
        return 1

    client = NotionClient(token, transport=transport)

    # In mock mode, seed a couple of pages so the skill has something to do.
    if args.mock and isinstance(transport, MockTransport):
        _seed_mock_pages(transport, args.database, plan)

    out, close_after = open_output(args.output)
    summary = {"matched": 0, "changed": 0, "warnings": 0, "errors": 0}

    try:
        for page in _iter_limited(client.iter_query(args.database, filter_=filter_obj), args.limit):
            summary["matched"] += 1
            changes = compute_changes(page, plan)
            has_change = bool(changes["properties"]) or changes["archived"] is not None
            record: Dict[str, Any] = {
                "page_id": page.get("id"),
                "title": title_of(page),
                "diff": changes["diff"],
                "warnings": changes["warnings"],
                "applied": False,
            }
            if changes["warnings"]:
                summary["warnings"] += 1

            if has_change and args.apply:
                try:
                    client.update_page(
                        page["id"],
                        properties=changes["properties"] or None,
                        archived=changes["archived"],
                    )
                    record["applied"] = True
                    summary["changed"] += 1
                except NotionError as err:
                    record["error"] = str(err)
                    summary["errors"] += 1
            elif has_change:
                summary["changed"] += 1  # "would change"
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
    finally:
        if close_after:
            out.close()

    summary["mode"] = "apply" if args.apply else "dry-run"
    sys.stderr.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return 0 if summary["errors"] == 0 else 1


def _iter_limited(it: Iterable[Dict[str, Any]], limit: int) -> Iterable[Dict[str, Any]]:
    count = 0
    for item in it:
        if count >= limit:
            sys.stderr.write(f"[bulk-edit] hit --limit={limit}; stopping\n")
            return
        count += 1
        yield item


def _seed_mock_pages(transport: MockTransport, db_id: str, plan: Dict[str, Any]) -> None:
    """Populate the mock DB with 3 pages so dry-run has something to chew on.

    The goal is demo-ability, not fidelity. Each page gets the properties
    referenced by the plan, pre-populated with 'old' values that differ
    from the plan's target.
    """
    if db_id in {p.get("_parent_database") for p in transport.pages.values()}:
        return
    prop_templates: Dict[str, Any] = {"Name": {"type": "title", "title": [{"type": "text", "text": {"content": ""}, "plain_text": ""}]}}
    for prop_name, value in (plan.get("set_properties") or {}).items():
        # Seed with an empty version of the same property type.
        ntype = next(iter(value)) if isinstance(value, dict) and value else "rich_text"
        prop_templates[prop_name] = {"type": ntype, ntype: None if ntype not in {"rich_text", "multi_select"} else []}
    for prop_name in (plan.get("append_multi_select") or {}):
        prop_templates[prop_name] = {"type": "multi_select", "multi_select": []}

    for i in range(3):
        props = deepcopy(prop_templates)
        props["Name"]["title"][0]["text"]["content"] = f"Mock page {i + 1}"
        props["Name"]["title"][0]["plain_text"] = f"Mock page {i + 1}"
        transport.pages[f"mock-page-{i}"] = {
            "object": "page",
            "id": f"mock-page-{i}",
            "archived": False,
            "properties": props,
            "last_edited_time": "2026-01-01T00:00:00.000Z",
            "_parent_database": db_id,
        }


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
