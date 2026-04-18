---
name: notion-sync
description: Use when the user wants to keep a Notion database and a local folder of Markdown files in sync — "sync my Reading Log DB to ~/notes/reading", "pull Notion to Markdown", "push these .md files back to Notion", "Notion と ~/docs を同期". Supports pull / push / both. Uses last-write-wins on the page's `last_edited_time` vs. the file's mtime; losing side is snapshotted to `.notion-sync/conflicts/`. Round-trip is lossy for complex block types (see TOOLBOX.md §7). Do NOT use for one-shot bulk edits (use `notion-bulk-edit`) or initial schema creation (use `notion-schema-setup`).
---

# notion-sync

Two-way sync between a Notion database and a local directory of
Markdown files. Each Notion page ↔ one `.md` file. State (per-page
cursor and content hash) is stored under `.notion-sync/state.json`
inside the local directory.

## When to use

- "sync database <id> to ~/notes/reading"
- "pull Notion DB down as Markdown"
- "push my edits in ~/bugs back into Notion"

Do NOT trigger when:

- The user just wants a one-off bulk mutation (`notion-bulk-edit`).
- The user needs true real-time sync — this plugin polls only.

## Directions

- `pull` — Notion is the source of truth. Files on disk that don't
  exist in Notion are **ignored** (not deleted).
- `push` — Local is the source of truth. Pages in Notion that don't
  exist locally are **ignored** (not archived).
- `both` — Compare both sides; whichever is newer wins per-page. If
  both changed since the last sync, the losing side is snapshotted to
  `conflicts/` and the winner still overwrites the other side.

## File layout

```
<local-dir>/
  <page_id>.md              # one file per page
  .notion-sync/
    state.json              # {"pages": {page_id: {mtime, hash}}}
    conflicts/
      <page_id>-<ts>-notion.json   # snapshot of Notion side
      <page_id>-<ts>-local.md      # snapshot of local side
```

### Markdown file format

Front matter is YAML-flavored minimal (parsed with a tiny stdlib
parser — no external dep). Body is the page's plaintext content.

```
---
notion_page_id: abcd-1234-...
title: Reading: The Overstory
last_edited_time: 2026-04-18T09:21:00.000Z
---

Body text here, paragraph by paragraph.
```

Only `paragraph` and `heading_1/2/3` blocks are round-tripped in V1.
Other block types (to_do, toggle, callout, etc.) are preserved
read-only on pull and skipped on push. This is documented in
TOOLBOX.md §4 as a known limitation.

## Prerequisites

- Python 3.8+. stdlib only.
- `NOTION_TOKEN` env var, or `--mock`.
- The target database must be shared with the integration.

## How to run

```
python skills/notion-sync/sync.py \
    --database <DB_ID> \
    --dir ~/notes/reading \
    --direction both \
    [--dry-run] [--mock]
```

| Flag | Purpose |
| --- | --- |
| `--database` | Database ID. Required. |
| `--dir` | Local directory. Created if absent. Required. |
| `--direction` | `pull`, `push`, or `both`. Default `both`. |
| `--dry-run` | Show the change manifest; don't write anywhere. |
| `--mock` | In-memory transport; seeds a 2-page demo DB. |
| `--limit` | Max pages to process per run. Default 1000. |

## How to present results

1. Parse the JSON summary on stdout:
   `{"pulled": N, "pushed": M, "conflicts": K, "skipped": S}`.
2. List conflicts with their snapshot paths so the user can merge.
3. If `--dry-run` was used, remind the user to re-run without it.

## Failure handling

- **Dir path invalid** — create `dir` on demand; if it exists but is a
  file, fail with a clear error.
- **Page missing on Notion during push** — log and skip; don't create.
  Recreating pages deleted upstream needs explicit user confirmation.
- **Both sides changed since last sync** — snapshot both to
  `conflicts/` and apply the newer one as the surviving version.
- **state.json corrupted** — log the parse error, back the file up as
  `state.json.broken-<ts>`, and do a full re-baseline.
