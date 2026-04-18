# TOOLBOX — notion-plugin

セッション開始時に目を通す実装ノート。Notion REST API v1 を叩くうえで
繰り返し踏む落とし穴を、出典つきで恒久化する場所。記述は
「何を／なぜ／どう」の順で。

---

## 1. 認証とバージョンヘッダ

### 1.1 必須ヘッダは 2 つ

```
Authorization: Bearer <NOTION_TOKEN>
Notion-Version: 2022-06-28
Content-Type: application/json
```

`Notion-Version` を省くと `400 Bad Request` が返る。新しいバージョン
（例：`2025-09-03`）は relation rollups 等の挙動が変わるため、
後方互換が取れている `2022-06-28` を plugin のデフォルトにする。

出典：[Notion API - Versioning](https://developers.notion.com/reference/versioning)

### 1.2 Integration token は内部統合用

- `NOTION_TOKEN` 環境変数から読む（V1）。
- 対象のページ・DB を integration と **明示的に共有** しないと
  `404 Not Found` または `object_not_found` が返る（権限モデル）。
- 共有は UI 側で実施するしかない。API では自分自身に共有できない。
- OAuth は V2 で対応予定（`DESIGN.md` 参照）。

出典：[Authorization](https://developers.notion.com/reference/authentication)

---

## 2. レート制限

### 2.1 上限は「平均 3 requests / second / integration」

- 実装上はトークンバケット（容量 3、補充 3/秒）で十分。
- 429 を返すときは `Retry-After`（秒）ヘッダに従うこと。
  Notion は動的にバックオフ時間を計算している。
- 5xx は指数バックオフ（base 500ms、最大 5 回）。

出典：[Request limits](https://developers.notion.com/reference/request-limits)

### 2.2 バルク操作では必ずレート制限を通す

`notion-bulk-edit` / `notion-sync` は数百ページを更新する。
クライアント側で律速しないと、途中で 429 を食らってログが濁る。
`NotionClient` が唯一の HTTP ゲート — 直接 `urllib` を呼ばない。

---

## 3. ページネーション

### 3.1 `has_more` / `next_cursor` / `start_cursor`

- list 系エンドポイント（`POST /databases/{id}/query`、
  `GET /blocks/{id}/children` 等）はすべてカーソルページネーション。
- リクエスト側は `start_cursor`（省略可）と `page_size`（最大 100）。
- レスポンス側は `has_more`（bool）と `next_cursor`（str or null）。
- **query** だけは POST で body に `start_cursor` を入れる。GET 系は
  クエリパラメータ。クライアント側で分岐する必要がある。

出典：[Pagination](https://developers.notion.com/reference/intro#pagination)

### 3.2 全件ロードはしない。イテレータを返す

`NotionClient.iter_query(db_id, filter=...)` は generator。
スキル側で `for page in client.iter_query(...)` と書けば
メモリに乗らない。`list()` で展開したくなったら立ち止まる。

---

## 4. リッチテキストとブロック

### 4.1 `rich_text` の要素上限

- 1 つの `rich_text` プロパティに含められる要素は **100 個まで**。
- 1 要素の `content` は **2000 文字まで**。
- 超えると `validation_error`。長文は要素分割して送る。

出典：[Request limits - Size limits](https://developers.notion.com/reference/request-limits#size-limits)

### 4.2 ブロックの階層追加は再帰で

- `POST /blocks/{id}/children` は 1 コールで 100 ブロックまで。
- ネストした children は **レスポンスの子 ID を使って追加コール** を
  打つ必要がある（API 1 発では深いツリーを作れない）。
- `notion-template-instantiate` はこの再帰を隠蔽する。

出典：[Append block children](https://developers.notion.com/reference/patch-block-children)

---

## 5. プロパティスキーマ推論（schema-setup）

### 5.1 サポートするプロパティタイプ（V1）

| 推論キーワード | Notion type |
| --- | --- |
| name / title / 見出し | `title` |
| status / state / ステータス | `status` |
| tags / labels / タグ | `multi_select` |
| date / due / 締切 | `date` |
| url / link | `url` |
| owner / assignee / 担当 | `people` |
| notes / description / メモ | `rich_text` |
| priority / 優先度 | `select` |
| done / 完了 | `checkbox` |
| count / number / 数 | `number` |

マッピング外のキーワードは `rich_text` にフォールバック。
ユーザが `--dry-run` で確認できる形にする。

出典：[Database properties](https://developers.notion.com/reference/property-object)

### 5.2 `title` プロパティは **必ず 1 つだけ**

DB 作成時、`title` type のプロパティが **0 個**でも **2 個以上**でも
`validation_error`。スキーマ推論は最初の 1 個を `title` に昇格させ、
残りは `rich_text` に降格させる。

出典：[Create a database - properties](https://developers.notion.com/reference/create-a-database)

---

## 6. アーカイブとソフトデリート

### 6.1 削除 = `archived: true` の PATCH

- ハードデリートは API にない。`PATCH /pages/{id}` に
  `{"archived": true}` を送る。
- 復元も同じ経路で `{"archived": false}`。
- `notion-sync` の「ローカルで消した → Notion でも消す」挙動は
  archive で実装する。conflicts/ には最終スナップショットを JSON で
  残す。

出典：[Archive a page](https://developers.notion.com/reference/archive-a-page)

---

## 7. 同期の衝突解決（notion-sync）

### 7.1 比較軸は `last_edited_time` + 内容ハッシュ

- Notion 側の `last_edited_time` は秒精度の ISO 8601。
- ローカル側は `.notion-sync/state.json` に前回同期時の
  `(page_id, last_edited_time, content_sha256)` を保存する。
- ハッシュは rich_text を plain text 化してから SHA-256。書式差だけで
  誤検知しない。

### 7.2 衝突時のディレクトリ

```
.notion-sync/
  state.json            # 前回 cursor と各ページの (mtime, hash)
  conflicts/
    <page_id>-<timestamp>-notion.json   # Notion 側のスナップショット
    <page_id>-<timestamp>-local.md      # ローカル側
```

last-write-wins で勝った側がソースに反映、負けた側は
`conflicts/` に退避して終了。ユーザが手動でマージする。

---

## 8. 開発サイクル

- コードを触る前に該当箇所の `DESIGN.md` / このファイルを読む。
- 実 API なしで検証したいときは `skills/_lib/mock_transport.py` の
  `MockTransport` を `NotionClient(transport=MockTransport(...))` に
  注入する。E2E は token が手に入ってから追加。
- 発見したハマりは **その場で** このファイルに追記。出典 URL は必須。
  「たぶんこう」は書かない。

---

## 9. 参考リンク集

- [Notion API Reference](https://developers.notion.com/reference/intro)
- [Notion API Changelog](https://developers.notion.com/page/changelog)
- [Versioning](https://developers.notion.com/reference/versioning)
- [Request limits](https://developers.notion.com/reference/request-limits)
- [Pagination](https://developers.notion.com/reference/intro#pagination)
- [Property object](https://developers.notion.com/reference/property-object)
- [Block object](https://developers.notion.com/reference/block)
