---
name: notion-schema-setup
description: Use when the user wants to create a new Notion database from a natural-language brief — "set up a Notion database for tracking X", "make me a Notion DB with these columns", "Notion にタスク管理 DB を作って". Infers property types from the brief (title / status / tags / date / people / number / select / checkbox / url / rich_text) and creates the database via the Notion REST API. Supports `--dry-run` so the user can inspect the inferred schema before committing. Do NOT use for editing existing databases (that is notion-bulk-edit) or for creating single pages inside an existing database (that is notion-template-instantiate).
---

# notion-schema-setup

Creates a new Notion database from a natural-language brief plus an
optional list of example column names. Property types are inferred with
a keyword map (see TOOLBOX.md §5.1) and the resulting schema is shown
to the user before the database is actually created.

## When to use

Trigger when the user wants a *new* database:

- "Notion に読書ログの DB を作って。タイトル・著者・評価・読了日"
- "set up a Notion database for bug tracking with priority, status,
  assignee, and due date"
- "make me a Notion project tracker"

Do NOT trigger when:

- The user wants to *edit* many existing pages (use `notion-bulk-edit`).
- The user has an existing template page and wants to fill it (use
  `notion-template-instantiate`).

## Prerequisites

- Python 3.8+. stdlib only.
- `NOTION_TOKEN` environment variable set to the integration secret.
- The parent page must already be shared with the integration in the
  Notion UI (see TOOLBOX.md §1.2).

For offline development without a token, pass `--mock`; the script
will drive an in-memory `MockTransport` and print the would-be request.

## How to run

```
python skills/notion-schema-setup/schema_setup.py \
    --parent-page <PAGE_ID> \
    --title "Reading Log" \
    --columns title,author,rating,status,finished_on,tags,notes \
    --dry-run
```

Drop `--dry-run` to actually create the database. Useful flags:

| Flag | Purpose |
| --- | --- |
| `--parent-page` | Notion page ID that will own the new database. Required unless `--mock`. |
| `--title` | Human-readable title of the database. Required. |
| `--columns` | Comma-separated list of column names (English or Japanese). Required. |
| `--brief` | Optional free-text description — reserved for future LLM-driven inference. |
| `--dry-run` | Print the inferred schema and exit without calling the API. |
| `--mock` | Use an in-memory transport. Prints the same schema and a fake DB id. |
| `--output` | Path to write the full JSON result (default: stdout). |

## Property-type inference (keyword map)

| Column keyword (any case, JP/EN mixed ok) | Notion type |
| --- | --- |
| name / title / タイトル / 見出し | `title` |
| status / state / ステータス / 状態 | `status` |
| tags / labels / タグ | `multi_select` |
| priority / 優先度 | `select` |
| date / due / deadline / 締切 / 日付 / 日 | `date` |
| url / link / リンク | `url` |
| owner / assignee / 担当 / 担当者 | `people` |
| notes / description / memo / メモ / 説明 | `rich_text` |
| done / 完了 / チェック | `checkbox` |
| count / number / rating / 数 / 評価 | `number` |
| (anything else) | `rich_text` |

The first `title`-mapped column is promoted to Notion's required `title`
property; further title-like columns are demoted to `rich_text` so the
database can be created (Notion rejects ≠1 title columns; see
TOOLBOX.md §5.2).

## How to present results

1. Parse the JSON printed to stdout.
2. Show the user a short table: column name → inferred Notion type.
3. Link to the new database using the `url` field from the response
   (the real API includes `url`; the mock does not, so omit if null).
4. If the user wanted an edit workflow next, suggest `notion-bulk-edit`.

## Failure handling

- **Missing token** — tell the user to set `NOTION_TOKEN` or re-run with
  `--mock`. Do not prompt for the token in chat.
- **404 from parent page** — the most common cause is that the
  integration hasn't been shared with the page. Suggest sharing it.
- **validation_error on properties** — print the Notion error body; the
  user needs to rename a column or remove ambiguity.
