# notion-plugin — Design Document

> Status: V1 implemented (5 skills, stdlib-only Python). OAuth and richer block-type round-trip deferred to V2. This document captures both the original design intent and the shipped behavior.

## Architecture Overview

The plugin pairs a thin **Notion REST API v1 client** with the **Claude API** for natural-language-to-schema translation.

```
User brief (natural language)
  ↓
Claude (schema inference / transformation planning)
  ↓
Notion REST API v1 (databases, pages, blocks)
  ↓
Local filesystem (for sync scenarios)
```

Key principles:
- Stateless skills — each invocation reads current Notion state, acts, and exits.
- Dry-run mode on every mutating skill for safety on large workspaces.
- Structured logs (JSONL) so multi-step runs are auditable.

## Skill-by-skill Plan

### 1. notion-schema-setup
- **Inputs:** natural-language brief describing the database purpose, example rows (optional), parent page ID.
- **Outputs:** created database ID, property map, summary report.
- **API calls:** `POST /v1/databases` with inferred property schema.
- **Auth:** internal integration token (env `NOTION_TOKEN`).

### 2. notion-bulk-edit
- **Inputs:** database ID, filter query, transformation instructions (natural language), dry-run flag.
- **Outputs:** per-page diff log, count of pages changed.
- **API calls:** `POST /v1/databases/{id}/query` (paginated), `PATCH /v1/pages/{id}`.
- **Auth:** `NOTION_TOKEN`.

### 3. notion-sync
- **Inputs:** database ID, local directory, sync direction (`pull` | `push` | `both`).
- **Outputs:** change manifest, conflict report.
- **API calls:** `POST /v1/databases/{id}/query`, `GET /v1/blocks/{id}/children`, `PATCH` / `POST` pages and blocks.
- **Auth:** `NOTION_TOKEN`.

### 4. notion-cross-reference
- **Inputs:** database ID(s), relation property name or "auto", similarity threshold.
- **Outputs:** proposed relations, created relations after confirmation.
- **API calls:** query + patch pages to populate relation properties.
- **Auth:** `NOTION_TOKEN`.

### 5. notion-template-instantiate
- **Inputs:** template page ID, structured payload (JSON or natural language), target parent.
- **Outputs:** created page ID(s), population report.
- **API calls:** `GET /v1/blocks/{id}/children` (template), `POST /v1/pages`, nested `PATCH` for blocks.
- **Auth:** `NOTION_TOKEN`.

## Authentication Design

- **V0/V1:** Internal integration tokens, read from `NOTION_TOKEN` environment variable. Users create an integration at <https://www.notion.so/my-integrations> and share target pages with it.
- **V2:** OAuth 2.0 flow for public distribution (deferred). Will require a hosted redirect endpoint and token storage strategy.

## Rate Limiting and Pagination

- Notion enforces ~3 requests/second per integration. The client uses a token-bucket limiter with automatic retry on HTTP 429 honoring `Retry-After`.
- All list endpoints are paginated via `start_cursor` / `has_more`; the client exposes async iterators so skills never load full result sets into memory.
- Exponential backoff on 5xx (base 500ms, max 5 retries).

## Conflict Resolution (notion-sync)

Two configurable strategies:
1. **last-write-wins** (default) — compare `last_edited_time` on both sides; newer wins, losing side is archived to `.notion-sync/conflicts/`.
2. **merge-prompt** — interactive mode; Claude produces a unified-diff-like preview and asks the user to accept Notion, accept local, or edit.

Sync state (cursor, content hashes) is stored in `.notion-sync/state.json` under the local directory.

## Known Limitations of the Notion API

- No access to comments v2 (threaded comments) — plugin will only surface page-level comments via the legacy endpoint if at all.
- Block type coverage is incomplete; synced/database-linked/AI blocks may round-trip lossily.
- No server-side full-text search beyond the `search` endpoint, which is shallow.
- Relation properties require both databases to be shared with the integration.
- No webhooks — sync must poll.

## Competitive Analysis

- As of 2026-04, **no plugin** exists under `anthropics/claude-plugins-official/external_plugins` that targets Notion.
- A Notion **MCP server** exists and handles low-level CRUD, but there is no higher-level plugin that bundles opinionated skills (schema inference, bulk edits, sync).
- Positioning: complement MCP — this plugin focuses on *workflows*, not raw API surface.

## Milestones

- **MVP (shipped):** `notion-schema-setup` + `notion-bulk-edit`.
- **V1 (shipped):** add `notion-sync` with last-write-wins strategy,
  `notion-cross-reference`, and `notion-template-instantiate`. Python
  stdlib only; mock transport for offline development.
- **V2 (planned):** OAuth 2.0 authentication, block archival for true
  push-replace in `notion-sync`, cross-database cross-referencing,
  wider block-type round-trip coverage (to\_do / toggle / callout /
  table), and LLM-driven schema inference for free-text briefs.
