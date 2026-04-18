# notion-plugin

Notion integration for Claude. Five composable skills that let an agent
set up databases, make bulk edits, sync Markdown, cross-reference
pages, and instantiate templates — all through the stable Notion REST
API v1, with no external Python dependencies.

## Skills

| Skill | What it does |
| --- | --- |
| [`notion-schema-setup`](skills/notion-schema-setup/) | Create a new Notion database from a brief + column list. Keyword-based type inference with a dry-run preview. |
| [`notion-bulk-edit`](skills/notion-bulk-edit/) | Query a database, apply a JSON transformation plan, and patch pages. Dry-run by default. |
| [`notion-sync`](skills/notion-sync/) | Two-way sync a database with a local Markdown directory. Last-write-wins with conflict snapshots. |
| [`notion-cross-reference`](skills/notion-cross-reference/) | Auto-populate a self-referencing relation property with Jaccard-scored neighbors. |
| [`notion-template-instantiate`](skills/notion-template-instantiate/) | Clone a template page with `{{placeholder}}` substitution. Single or batch. |

Each skill folder ships a `SKILL.md` (trigger semantics + CLI), a
Python entrypoint, and a short `README.md` with copy-paste quickstarts.

## Design and limits

Read [`DESIGN.md`](DESIGN.md) for the architecture rationale and
[`TOOLBOX.md`](TOOLBOX.md) for the Notion-API landmines this plugin
navigates (rate limits, pagination, rich-text size caps, archive
semantics, sync conflict rules).

## Requirements

- Python 3.8+ on PATH. **No pip installs needed** — every script uses
  the stdlib only (`urllib`, `json`, `argparse`, `hashlib`, …).
- A Notion integration token, exported as `NOTION_TOKEN`.
  Create one at <https://www.notion.so/my-integrations>, then share
  the target pages / databases with the integration in the Notion UI
  (see `TOOLBOX.md` §1.2).
- For offline development without a token, every skill accepts
  `--mock` and drives an in-memory transport with a small seed dataset.

## Quickstart

```bash
git clone https://github.com/RintaroMatsumoto/notion-plugin.git
cd notion-plugin

# No install step. Just run the skill entrypoints.
python skills/notion-schema-setup/schema_setup.py \
    --title "Reading Log" \
    --columns "title,author,rating,status,finished_on,tags,notes" \
    --mock
```

Drop `--mock` and set `NOTION_TOKEN` + `--parent-page <id>` to go
live.

## Testing

```
python -m unittest discover -s tests -v
```

The test suite uses `MockTransport` only — no token required, no
network traffic.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). The design is relatively
stable at V1; OAuth and richer block-type coverage are tracked as V2
work in `DESIGN.md`.

## License

MIT.

---

Part of a multi-plugin portfolio alongside
[`arxiv-research-toolkit`](https://github.com/RintaroMatsumoto/arxiv-research-toolkit),
[`programmatic-video-gen`](https://github.com/RintaroMatsumoto/programmatic-video-gen),
and [`companion-spec`](https://github.com/RintaroMatsumoto/companion-spec).
