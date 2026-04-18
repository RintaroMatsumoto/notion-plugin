---
name: notion-template-instantiate
description: Use when the user has a Notion template page (or a database template) and wants to create new page(s) from it with placeholders filled from structured input — "create a meeting note from the Standup Template with today's date and attendees", "make 10 onboarding pages from this template, one per new hire", "テンプレートから今日のデイリーを作って". Reads the template's children blocks, substitutes `{{placeholder}}` tokens with values from a JSON payload, and creates the new page(s) via POST /v1/pages. Do NOT use for creating an empty page (call POST /v1/pages directly) or for filling form-style databases without a template (`notion-bulk-edit`).
---

# notion-template-instantiate

Copy a template page's content, substitute `{{placeholder}}` tokens,
and create one or more new pages. Placeholders are plain string
replacements; values come from a JSON payload.

## When to use

- "create a meeting note from the Standup Template for today"
- "make 10 onboarding pages from <template>, one per row in this CSV"
- "テンプレートから今日のデイリーを作って"

Do NOT trigger when:

- There is no template — a plain `POST /pages` is simpler.
- The user just wants to edit existing pages (use `notion-bulk-edit`).

## Prerequisites

- Python 3.8+. stdlib only.
- `NOTION_TOKEN` or `--mock`.
- Both the template page and the target parent (page or database)
  must be shared with the integration.

## How to run

Single instantiation:

```
python skills/notion-template-instantiate/template_instantiate.py \
    --template <TEMPLATE_PAGE_ID> \
    --parent-database <DB_ID> \
    --payload '{"title": "2026-04-20 Standup", "date": "2026-04-20", "attendees": "Alice, Bob"}'
```

Batch instantiation from a JSONL file:

```
python skills/notion-template-instantiate/template_instantiate.py \
    --template <TEMPLATE_PAGE_ID> \
    --parent-database <DB_ID> \
    --payload-file hires.jsonl
```

| Flag | Purpose |
| --- | --- |
| `--template` | Template page id whose children blocks will be cloned. Required. |
| `--parent-database` | Database to create the page(s) in. One of `--parent-database` / `--parent-page` required. |
| `--parent-page` | Page to create the child page(s) under. |
| `--payload` | Inline JSON object with substitution values. |
| `--payload-file` | Path to a JSONL file (one object per line). One page is created per line. |
| `--title-field` | Which key in the payload should become the page title. Default `title`. |
| `--title-prop` | Name of the Notion title property (only relevant when `--parent-database` is set). Default: the DB's actual title property. |
| `--mock` | MockTransport; seeds a simple template. |
| `--dry-run` | Print the planned substitutions; don't call POST. |

## Placeholder syntax

`{{key}}` where `key` is a top-level field of the payload. Unknown
placeholders are left as-is and surfaced in the warnings list so the
user can spot typos. Substitution is done across every `rich_text`
element in every supported child block type (paragraph, heading\_1/2/3,
to\_do, bulleted\_list\_item, numbered\_list\_item, quote, callout).

## Output

```json
{
  "template": "…",
  "parent": {"database_id": "…"},
  "created": [
    {"page_id": "…", "title": "2026-04-20 Standup"}
  ],
  "warnings": ["unknown placeholder: {{unknown_key}}"]
}
```

## Failure handling

- **Template has 0 children** — creates an empty page with only the
  title. Warn the user.
- **Placeholder unknown** — left literal; warning emitted. The page is
  still created.
- **Block type not in allow-list** — cloned as-is with best effort,
  warning emitted. Synced / AI / database-linked blocks are skipped.
