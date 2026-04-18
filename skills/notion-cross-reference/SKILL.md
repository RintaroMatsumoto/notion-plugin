---
name: notion-cross-reference
description: Use when the user wants to automatically populate relation properties between pages in a Notion database — "link related bugs in the Bugs DB", "cross-reference my reading notes that share tags", "DB 内で関連するページを自動リンク". Scores page pairs with a Jaccard-style overlap over titles + tags, proposes relations, and (with `--apply`) writes them into a specified relation property. Dry-run by default. Do NOT use for creating new relation properties (that is `notion-schema-setup`) or for finding papers across databases you don't own.
---

# notion-cross-reference

Propose and (optionally) write cross-reference links between pages of
a single database. Similarity is a stdlib-only Jaccard on the union of
title tokens + multi_select tag names. No ML.

## When to use

- "link related bugs in this DB"
- "auto-cross-reference my reading notes"
- "DB 内の関連記事をつないで"

Do NOT trigger when:

- No relation property exists yet — run `notion-schema-setup` or add
  it in the UI first.
- The user wants cross-database links — V1 is single-database only.

## Prerequisites

- Python 3.8+. stdlib only.
- `NOTION_TOKEN` or `--mock`.
- A `relation` property already defined on the database, self-referencing.

## How to run

```
python skills/notion-cross-reference/cross_reference.py \
    --database <DB_ID> \
    --relation-property "Related" \
    --threshold 0.3 \
    --top-k 5 \
    [--apply] [--mock]
```

| Flag | Purpose |
| --- | --- |
| `--database` | Target database id. Required. |
| `--relation-property` | Name of the self-referencing relation property. Required. |
| `--threshold` | Minimum Jaccard score. Default `0.3`. |
| `--top-k` | Keep at most this many relations per page. Default `5`. |
| `--tag-properties` | Comma-separated multi_select property names to fold into similarity. Default: all multi_select props. |
| `--apply` | Actually PATCH pages. Default: dry-run. |
| `--mock` | Use MockTransport; seeds a small demo DB. |

## Similarity model

For pages `A` and `B`:

```
tokens(A) = lowercase tokens of title  +  names of each multi_select option
similarity(A, B) = |tokens(A) ∩ tokens(B)|  /  |tokens(A) ∪ tokens(B)|
```

Pages are considered a pair if `similarity >= threshold`. For each
page, keep the top-K neighbors by score.

## Output

One JSONL record per page:

```json
{
  "page_id": "abc",
  "title": "…",
  "proposed": [
    {"page_id": "def", "title": "…", "score": 0.42},
    …
  ],
  "applied": false
}
```

## Failure handling

- **Relation property missing** — fail fast with a clear error.
- **No multi_select props and titles identical** — threshold will trip
  trivially; warn the user to add `--tag-properties` or raise the bar.
