# notion-sync

One directory, one database, Markdown as lingua franca.

See `SKILL.md` for the full contract, including the file layout and
the known limitations (V1 round-trips only `paragraph` +
`heading_1/2/3`).

## Quickstart (mock)

```
python sync.py --database demo-db --dir ./sync-demo --mock
```

Creates `sync-demo/*.md` from the seeded pages and writes
`sync-demo/.notion-sync/state.json`.

## Real usage

```
export NOTION_TOKEN=ntn_xxx...
python sync.py --database <db_id> --dir ~/notes/reading --direction both
```

- `--direction pull` to mirror Notion to disk only.
- `--direction push` to mirror disk to Notion only.
- `--direction both` (default) for two-way.
- `--dry-run` to preview the change manifest without writing.

## Known limits (V1)

- Only paragraph / heading\_1 / heading\_2 / heading\_3 blocks are
  round-tripped. Toggles, callouts, to-dos are preserved read-only on
  pull and **skipped** on push.
- Push is additive: new Markdown content is appended as new blocks
  rather than replacing the existing blocks. Proper replace requires
  child-block archival, planned for V2.
- Conflict resolution is last-write-wins on Notion's
  `last_edited_time` vs. local mtime, snapshotting losers under
  `.notion-sync/conflicts/`.
