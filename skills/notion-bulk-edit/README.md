# notion-bulk-edit

Apply a JSON transformation plan to every page in a Notion database.
Dry-run by default — nothing is mutated until `--apply` is passed.

See `SKILL.md` for the full CLI and plan-file schema.

## Quickstart (mock)

`plan.json`:

```json
{
  "set_properties": {"Status": {"status": {"name": "Done"}}},
  "append_multi_select": {"Tags": ["2026-Q2"]}
}
```

```
python bulk_edit.py --database demo-db --plan plan.json --mock
```

This will seed the mock transport with three demo pages and print the
diff to stdout. To actually commit the same plan against a real DB:

```
export NOTION_TOKEN=ntn_xxx...
python bulk_edit.py --database <db_id> --plan plan.json --apply
```

## Output

One JSONL record per page, on stdout (or `--output`):

```json
{"page_id": "...", "title": "Mock page 1", "diff": ["set Status = ..."], "warnings": [], "applied": false}
```

Summary JSON is written to stderr on completion.
