> これは Anthropic の Claude Plugins 公式ディレクトリ提出フォームに手動で貼り付けるためのドラフトである。各フィールドはフォーム項目に 1:1 対応している。
>
> 提出順の推奨: **2 番目**（v0.1.0 タグ済み、stdlib-only Python で 5 skills、`--mock` 付きで通常提出可）

---

## notion-plugin

- **Plugin name** (kebab-case): notion-plugin
- **Public repository URL**: https://github.com/RintaroMatsumoto/notion-plugin
- **Latest tag**: v0.1.0
- **Author**: Rintaro Matsumoto
- **License**: MIT
- **Homepage**: https://github.com/RintaroMatsumoto/notion-plugin
- **Category (candidate)**: productivity — the plugin targets knowledge-management workflows in Notion (schema, bulk edit, sync, cross-ref, templates), which is the canonical productivity surface.
- **Keywords (5-8)**: notion, knowledge-management, automation, markdown-sync, bulk-edit, templates, productivity, stdlib-only

### Short tagline (<=60 chars, English)
Five Notion skills: schema, bulk edit, sync, cross-ref, templates.

### Description (plain English, ~450 chars)
A Notion integration for Claude. Five composable skills drive the stable Notion REST API v1: create a database from a brief with keyword-based type inference, apply a JSON transformation plan across pages with dry-run preview, two-way sync a database with a local Markdown directory under last-write-wins with conflict snapshots, auto-populate a self-referencing relation with Jaccard neighbors, and clone a template page with `{{placeholder}}` substitution for single or batch runs. Every script is stdlib-only Python and ships a `--mock` mode that drives an in-memory transport so reviewers can try each skill without a Notion token.

### Differentiators (3, English)
- Zero pip installs. Every skill uses Python stdlib (`urllib`, `json`, `hashlib`), so the plugin runs on a vanilla Python 3.8+.
- Each skill has a `--mock` mode with a seed dataset, enabling offline demos, CI tests, and reviewer trials without credentials.
- Dry-run is the default for destructive operations (bulk edit, sync); a `TOOLBOX.md` documents the Notion-API landmines (rate limits, pagination, rich-text caps, archive semantics).

### Included skills (from plugin.json / skills/)
- notion-schema-setup - Create a database from a brief + column list with type inference and dry-run preview.
- notion-bulk-edit - Query a database and apply a JSON transformation plan; dry-run by default.
- notion-sync - Two-way sync a database with a local Markdown dir; last-write-wins + conflict snapshots.
- notion-cross-reference - Auto-populate a self-referencing relation with Jaccard-scored neighbors.
- notion-template-instantiate - Clone a template page with `{{placeholder}}` substitution; single or batch.

### Reviewer trial path (<=5 lines)
1. `/plugin install notion-plugin`
2. Run any skill with `--mock` to exercise the in-memory transport without credentials.
3. For a live run: export `NOTION_TOKEN` and share the target pages with the integration.
4. Say "create a Notion database for tracking podcast episodes".
5. Say "bulk-edit all pages where Status=Draft to set Priority=High (dry run)".

### Notes / Caveats
- Requires Python 3.8+ on PATH; no pip installs.
- Notion integration token (`NOTION_TOKEN`) required for live runs; pages/databases must be shared with the integration in the Notion UI.
