# notion-schema-setup

Create a new Notion database from a natural-language brief + column list.
Inference is keyword-based and transparent — run with `--dry-run` to see
the schema before the database is actually created.

See `SKILL.md` for trigger semantics and TOOLBOX.md §5 in the plugin
root for the property-type mapping rationale.

## Quickstart

Offline, no token needed:

```
python schema_setup.py \
    --title "Reading Log" \
    --columns "title,author,rating,status,finished_on,tags,notes" \
    --mock
```

Against the real API:

```
export NOTION_TOKEN=ntn_xxx...
python schema_setup.py \
    --parent-page 1a2b3c4d5e6f7890abcdef1234567890 \
    --title "Reading Log" \
    --columns "title,author,rating,status,finished_on,tags,notes"
```

## Output shape

```json
{
  "title": "Reading Log",
  "parent_page": "1a2b...",
  "schema_preview": [
    {"name": "title", "type": "title"},
    {"name": "author", "type": "rich_text"},
    {"name": "rating", "type": "number"}
  ],
  "status": "created",
  "database_id": "abcd-..."
}
```
