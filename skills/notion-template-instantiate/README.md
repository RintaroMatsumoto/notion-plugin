# notion-template-instantiate

Clone a Notion template page's children, substitute `{{placeholder}}`
tokens from a JSON payload, and create one or more new pages.

See `SKILL.md` for trigger phrases and the full CLI.

## Quickstart (mock)

```
python template_instantiate.py \
    --template tmpl-1 \
    --parent-database demo-db \
    --payload '{"title": "2026-04-20 Standup", "date": "2026-04-20", "attendees": "Alice, Bob"}' \
    --mock
```

## Batch instantiation

`hires.jsonl`:

```
{"title": "Onboarding: Alice", "name": "Alice", "start_date": "2026-05-01"}
{"title": "Onboarding: Bob", "name": "Bob", "start_date": "2026-05-08"}
```

```
python template_instantiate.py \
    --template <tmpl_id> \
    --parent-database <db_id> \
    --payload-file hires.jsonl
```

## Supported block types

paragraph, heading\_1/2/3, to\_do, bulleted\_list\_item,
numbered\_list\_item, quote, callout. Other block types are skipped
with a warning — synced/AI/database-linked blocks in particular do
not round-trip cleanly (see TOOLBOX.md §4.2).
