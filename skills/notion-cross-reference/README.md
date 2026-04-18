# notion-cross-reference

Score every pair of pages in a single database with a stdlib-only
Jaccard over (title tokens ∪ tag option names), and write the top-K
matches into a self-referencing `relation` property.

See `SKILL.md` for the scoring model, trigger semantics, and required
schema (a self-referencing relation property must already exist).

## Quickstart (mock)

```
python cross_reference.py \
    --database demo-db \
    --relation-property Related \
    --threshold 0.3 --top-k 2 \
    --mock
```

## Real usage

```
export NOTION_TOKEN=ntn_xxx...
python cross_reference.py \
    --database <db_id> \
    --relation-property Related \
    --threshold 0.3 --top-k 5 \
    --apply
```
