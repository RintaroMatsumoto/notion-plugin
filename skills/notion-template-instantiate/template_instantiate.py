#!/usr/bin/env python3
"""
template_instantiate.py — Clone a Notion template page, substitute
{{placeholders}} in its rich_text, and create one or more new pages.

Exit codes:
    0 = success (incl. dry-run)
    1 = Notion API failure
    2 = argparse
    3 = payload / input error
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _lib.notion_client import (  # noqa: E402
    NotionClient,
    MockTransport,
    NotionError,
    rich_text_payload,
)


PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_\-]+)\s*\}\}")

SUPPORTED_TYPES = {
    "paragraph",
    "heading_1",
    "heading_2",
    "heading_3",
    "to_do",
    "bulleted_list_item",
    "numbered_list_item",
    "quote",
    "callout",
}


def substitute(text: str, payload: Dict[str, Any]) -> Tuple[str, List[str]]:
    warnings: List[str] = []

    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key in payload:
            return str(payload[key])
        warnings.append(f"unknown placeholder: {{{{{key}}}}}")
        return match.group(0)

    return PLACEHOLDER_RE.sub(replace, text), warnings


def transform_rich_text(rich_text: List[Dict[str, Any]], payload: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Substitute placeholders inside each rich_text element's content."""
    out: List[Dict[str, Any]] = []
    all_warnings: List[str] = []
    for item in rich_text or []:
        content = (item.get("text") or {}).get("content", "") or item.get("plain_text", "")
        new_content, warnings = substitute(content, payload)
        all_warnings.extend(warnings)
        element = deepcopy(item)
        element.setdefault("type", "text")
        element["text"] = {"content": new_content}
        # Drop cached plain_text so Notion re-derives it.
        element.pop("plain_text", None)
        out.append(element)
    return out, all_warnings


def clone_block(block: Dict[str, Any], payload: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    btype = block.get("type")
    if btype not in SUPPORTED_TYPES:
        return None, [f"unsupported block type skipped: {btype}"]
    body = block.get(btype) or {}
    new_rich, warnings = transform_rich_text(body.get("rich_text") or [], payload)
    new_body = deepcopy(body)
    new_body["rich_text"] = new_rich
    # Strip ids / child markers — POST requires a fresh skeleton.
    new_body.pop("children", None)
    return {"object": "block", "type": btype, btype: new_body}, warnings


def resolve_title_property(
    client: NotionClient,
    parent_database_id: Optional[str],
    override: Optional[str],
) -> Optional[str]:
    if not parent_database_id:
        return None
    if override:
        return override
    try:
        db = client.get_database(parent_database_id)
    except NotionError:
        return None
    for name, prop in (db.get("properties") or {}).items():
        if prop.get("type") == "title":
            return name
    return None


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Instantiate a Notion template page with placeholders.")
    p.add_argument("--template", required=True)
    p.add_argument("--parent-database", default=None)
    p.add_argument("--parent-page", default=None)
    p.add_argument("--payload", default=None, help="Inline JSON object.")
    p.add_argument("--payload-file", default=None, help="Path to JSONL file (one object per line) for batch creation.")
    p.add_argument("--title-field", default="title")
    p.add_argument("--title-prop", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--mock", action="store_true")
    return p.parse_args(argv)


def load_payloads(args: argparse.Namespace) -> List[Dict[str, Any]]:
    payloads: List[Dict[str, Any]] = []
    if args.payload:
        payloads.append(json.loads(args.payload))
    if args.payload_file:
        with open(args.payload_file, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    payloads.append(json.loads(line))
                except json.JSONDecodeError as err:
                    raise RuntimeError(f"--payload-file line {line_no}: {err}") from err
    return payloads


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    if not (args.parent_database or args.parent_page):
        sys.stderr.write("error: --parent-database or --parent-page required\n")
        return 3
    try:
        payloads = load_payloads(args)
    except (OSError, RuntimeError, json.JSONDecodeError) as err:
        sys.stderr.write(f"error: {err}\n")
        return 3
    if not payloads:
        sys.stderr.write("error: no payload provided (--payload or --payload-file)\n")
        return 3

    transport = MockTransport() if args.mock else None
    token = os.environ.get("NOTION_TOKEN") or ("mock-token" if args.mock else "")
    if not token:
        sys.stderr.write("error: NOTION_TOKEN not set (or pass --mock)\n")
        return 1
    client = NotionClient(token, transport=transport)

    if args.mock and isinstance(transport, MockTransport):
        _seed_mock(transport, args.template, args.parent_database)

    try:
        template_children = list(client.iter_block_children(args.template))
    except NotionError as err:
        sys.stderr.write(f"notion API error (template fetch): {err}\n")
        return 1

    title_prop = resolve_title_property(client, args.parent_database, args.title_prop)

    report: Dict[str, Any] = {
        "template": args.template,
        "parent": {"database_id": args.parent_database} if args.parent_database else {"page_id": args.parent_page},
        "created": [],
        "warnings": [],
        "dry_run": args.dry_run,
    }

    for payload in payloads:
        cloned: List[Dict[str, Any]] = []
        warnings: List[str] = []
        for block in template_children:
            new_block, block_warnings = clone_block(block, payload)
            warnings.extend(block_warnings)
            if new_block is not None:
                cloned.append(new_block)
        title_value = str(payload.get(args.title_field, "Untitled"))
        properties: Dict[str, Any] = {}
        if title_prop:
            properties[title_prop] = {"title": rich_text_payload(title_value)}
        record: Dict[str, Any] = {"title": title_value, "blocks": len(cloned), "warnings": warnings}

        if args.dry_run:
            report["created"].append(record)
            report["warnings"].extend(warnings)
            continue

        try:
            created = client.create_page(
                parent_database_id=args.parent_database,
                parent_page_id=args.parent_page,
                properties=properties,
                children=cloned,
            )
            record["page_id"] = created.get("id")
        except NotionError as err:
            record["error"] = str(err)
            report["created"].append(record)
            report["warnings"].extend(warnings)
            sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            return 1
        report["created"].append(record)
        report["warnings"].extend(warnings)

    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0


def _seed_mock(
    transport: MockTransport,
    template_id: str,
    parent_db: Optional[str],
) -> None:
    # Template page
    transport.pages[template_id] = {
        "object": "page",
        "id": template_id,
        "archived": False,
        "properties": {"Name": {"type": "title", "title": [{"type": "text", "text": {"content": "Standup Template"}, "plain_text": "Standup Template"}]}},
    }
    transport.blocks[template_id] = [
        {"object": "block", "id": "tb-1", "type": "heading_1", "heading_1": {"rich_text": [{"type": "text", "text": {"content": "Standup {{date}}"}}]}},
        {"object": "block", "id": "tb-2", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": "Attendees: {{attendees}}"}}]}},
        {"object": "block", "id": "tb-3", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": "Updates from the team"}}]}},
    ]
    # Parent database skeleton
    if parent_db and parent_db not in transport.databases:
        transport.databases[parent_db] = {
            "object": "database",
            "id": parent_db,
            "properties": {"Name": {"type": "title", "title": {}}},
        }


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
