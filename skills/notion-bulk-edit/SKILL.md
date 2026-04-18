---
name: notion-bulk-edit
description: Use when the user wants to apply the same change to many pages in a Notion database — "set status=Done on every page in the Bugs DB where priority=P1", "tag すべての読書ログに '2026' を追加", "bulk archive completed tasks". Queries the database (with a Notion filter), applies a JSON transformation plan (set properties / append tags / archive), and reports a per-page diff. `--dry-run` is ON by default for safety; pass `--apply` to actually commit changes. Do NOT use for creating new databases (`notion-schema-setup`) or for two-way sync (`notion-sync`).
---

# notion-bulk-edit

Query → transform → patch. Safe by default: `--dry-run` is the default
mode; you must pass `--apply` to actually mutate the workspace.

## When to use

- "set all rows with Status=Blocked to Status=Done in DB <id>"
- "append tag '2026-Q2' to every page in <id>"
- "archive すべての完了タスク in my Tasks DB"

Do NOT trigger when:

- There is no existing database (use `notion-schema-setup`).
- The user wants bidirectional sync with local files (use `notion-sync`).

## Prerequisites

- Python 3.8+. stdlib only.
- `NOTION_TOKEN` env var, or `--mock`.
- The target database must be shared with the integration.

## How to run

```
python skills/notion-bulk-edit/bulk_edit.py \
    --database <DB_ID> \
    --plan plan.json \
    --filter filter.json \
    [--apply] [--mock] [--output diff.jsonl]
```

Defaults are conservative: without `--apply` the script prints the diff
and exits without calling `PATCH /pages`.

### Plan file (`--plan`)

JSON describing what to change per page. Supported keys:

| Key | Meaning |
| --- | --- |
| `set_properties` | Object keyed by property name. Values are full Notion property objects (e.g. `{"select": {"name": "Done"}}`). Merged into each page's properties. |
| `append_multi_select` | Object keyed by property name. Value is a list of option names to append (deduped). |
| `archive` | Boolean; `true` archives, `false` un-archives. |

Example:

```json
{
  "set_properties": {
    "Status": {"status": {"name": "Done"}}
  },
  "append_multi_select": {
    "Tags": ["2026-Q2"]
  }
}
```

### Filter file (`--filter`)

Optional. Passed verbatim as the `filter` field to
`POST /databases/{id}/query`. See
<https://developers.notion.com/reference/post-database-query-filter>.
Omit to touch every page.

### Flags

| Flag | Purpose |
| --- | --- |
| `--database` | Target database id. Required. |
| `--plan` | Path to plan JSON. Required. |
| `--filter` | Path to filter JSON. Optional. |
| `--apply` | Actually call PATCH. Without this, runs in dry-run. |
| `--mock` | Use MockTransport. Implies no network. |
| `--output` | Write per-page diff lines (JSONL) to this path. Default stdout. |
| `--limit` | Hard cap on pages touched in one run (default 1000). |

## How to present results

1. Read the JSONL output. Each line is
   `{"page_id": "...", "title": "...", "changes": [...], "applied": bool}`.
2. Summarize: "N pages matched, M changed (K skipped — already matching)".
3. Warn if the run hit `--limit`.
4. If `--apply` was NOT passed, remind the user to re-run with `--apply`.

## Failure handling

- **Missing token** — tell the user; suggest `--mock` for preview.
- **filter.json invalid** — Notion returns `validation_error`; echo it.
- **429 rate limit** — the shared client handles it (TOOLBOX.md §2).
  No special handling needed here.
- **A plan references a property that doesn't exist** — the per-page
  diff logs `"error": "unknown property"` and keeps going. Surface a
  consolidated warning at the end.
