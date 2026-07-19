# NEWTONE RECORDS 新譜チェック — 仕様書

## 1. 概要

レコード店「**NEWTONE RECORDS**」の**新入荷レコードを日付ごとにチェック（消し込み）していく**ためのモバイル向け Web アプリ。

- **実体**: [index.html](../index.html) 単一ファイル（HTML + CSS + Vanilla JS を内包、外部 JS ライブラリなし）。
- **見た目**: ダークテーマ。`Courier Prime`（等幅・見出し）と `Noto Sans JP`（本文）の 2 フォントを Google Fonts から読み込み。
- **最適化**: スマホ前提。`env(safe-area-inset-*)` でノッチ対応、`apple-mobile-web-app-*` メタタグでホーム画面アプリ化に対応。
- **状態保存**: サーバーを持たず、すべて **localStorage** に保存するクライアント完結型。
- **レスポンシブ**: 幅 680px 未満はリスト↔詳細を横スライドで切り替える 1 カラム、680px 以上はサイドバー + 詳細の 2 カラム。

---

## 2. 画面と UI 構造

### ヘッダー（`#app-header`）
- 戻るボタン `‹`（スマホの詳細表示時のみ）
- タイトル「Newtone Records」/ サブ「新譜チェック」
- **♡ プールボタン**（お気に入り件数バッジ付き）→ プールページを開閉
- **⚙ 設定ボタン** → 設定ページを開閉

### 4 つの画面
| # | 要素 | 役割 |
|---|------|------|
| 1 | `#list-panel` | **ツリーナビ**（年 > 月 > 日）。年・月は開閉、日をタップで詳細へ。 |
| 2 | `#day-panel` | **日別レコード一覧**。進捗バー、全チェック/リセット、カテゴリ別のレコード行。 |
| 3 | `#pool-page` | **プール（お気に入り）ページ**。♡ したレコードの一覧・全削除。 |
| 4 | `#settings-page` | **設定ページ**。日付範囲を指定して過去データを取得。 |

### レコード行（`.record-item`）の構成
- チェック用の丸（`.check-circle`、チェック済みは `✓`）
- サムネイル画像（`.record-thumb`）
- アーティスト名 / タイトル / タグ（format・label・genre、および `USED`・`予約`）
- 「**Apple Music**」ボタン（後述）
- 外部リンク `↗`（`r.url` を開く）
- **♡ ハートボタン**（プールへの追加/削除）

---

## 3. データフロー

### 3-1. localStorage キー
| 定数 | キー | 内容 |
|------|------|------|
| `LS_DATA` | `nt_data` | レコード本体。`"日付|カテゴリ"` をキーにしたグループの辞書。 |
| `LS_CHECKED` | `nt_checked` | チェック済みレコード ID の辞書。 |
| `LS_FETCHED` | `nt_fetched` | 最終取得時刻（epoch ミリ秒）。 |
| `LS_POOL` | `nt_pool` | プール（お気に入り）レコードの辞書。 |
| `LS_REMOVED_IDS` | `nt_removed_ids` | 削除済みレコード ID（再取得しても復活させない）。 |
| `LS_REMOVED_DAYS` | `nt_removed_days` | 削除済み日付。 |
| `LS_UPDATED` | `nt_updated_at` | 最終更新時刻（サーバー同期の新旧判定に使用）。 |

`loadStorage` / `saveStorage` が JSON の読み書きを担当。

### 3-5. サーバー同期（iOS の localStorage 消失対策）
iOS Safari のホーム画面アプリは、約1週間開かないと **localStorage を丸ごと削除**することがある。
これに備え、チェック状態・お気に入り・削除履歴・レコード本体を Cloudflare Worker（KV）へ自動保存する。

- **保存先 Worker**: `state-worker/`（[worker.js](../state-worker/worker.js) / デプロイ手順は [README](../state-worker/README.md)）。`GET/PUT /state?key=<スロット名>`。
- **有効化**: `index.html` の `SYNC_API_BASE` に Worker の URL を設定すると有効。**空文字なら同期オフ**でこれまで通りローカルのみ動作（フェイルセーフ）。
- **同期ID**: `SYNC_KEY`（既定 `newtone-main`）をソースに埋め込む。localStorage が消えても ID は残るため、起動時に自動復元できる。
- **挙動**（`pullState` / `pushStateNow` / `scheduleSync`）:
  - 起動時に `pullState` でサーバー状態を取得。サーバーの方が新しい、またはローカルが空なら復元。
  - チェック等の変更後は `scheduleSync` が 2 秒デバウンスして `pushStateNow` で保存。
  - 1 台運用前提の「新しい方が勝ち（`updatedAt` 比較）」方式。

### 3-2. 通常データの取得（`fetchAndMerge`）
1. 起動時（`init`）に同ディレクトリの `data.json?t=<timestamp>` を `no-store` で fetch。
2. 既存レコード ID を集約し、**未知の ID だけ**を該当グループに追記マージ。
3. `removedDays` / `removedIds` に載っている日付・ID はスキップ（復活防止）。
4. `nt_data` と `nt_fetched` を保存。

### 3-3. 範囲取得（`triggerRangeScrape` → `mergeRangeGroups`）
設定ページから、Cloudflare Worker API 経由で任意の日付範囲を取得する。
- **API ベース**: `https://newtone-range-worker.dechnobow-newtone.workers.dev`
- 流れ:
  1. `GET /api/range/start?from=&to=` でジョブ開始 → `jobId` を受領。
  2. `GET /api/range/result?id=<jobId>` を **5 秒間隔で最大 24 回（約 2 分）ポーリング**。
  3. `status === 'done'` になったら `mergeRangeGroups` で `range` カテゴリとして反映。対象日付の既存 `range` データは置き換え、最新の取得日を自動選択して表示。
  4. `error` / タイムアウト時はステータス欄にエラー表示。

### 3-4. ツリー構築（`buildTree`）
`nt_data` を走査し `年 → 月 → 日 → レコード配列` の入れ子構造を生成。同一 ID は重複排除。

---

## 4. 主要機能

### チェック消し込み（`toggle` / `checkAll` / `uncheckAll`）
- レコード行タップで `checked[id]` をトグル → 見た目・カテゴリ件数・進捗バー・日バッジを更新。
- **その日の全レコードがチェック済みになると**、「リストから削除しますか？」の確認ダイアログ（`confirm`）を表示。

### 日の削除（`deleteDay`）
- 対象日を `removedDays` に、含まれるレコードを `removedIds` に記録（**再マージで復活しない**）。
- `nt_data` / `nt_checked` から該当日を除去し、ツリーからも削除してリスト画面へ戻る。

### プール / お気に入り（`togglePool` 他）
- ♡ ボタンでレコードを `pool` に追加/削除。件数はヘッダーのバッジに反映。
- プールページ（`renderPoolPage`）で一覧表示、`removeFromPool` で個別削除、`clearPool` で全削除（確認あり）。

### Apple Music 検索（`openAppleMusic`）
- タイトルから括弧書き `(...) （...） [...]` を除去し、`アーティスト + タイトル + "Apple Music"` を検索クエリ化。
- **`site:music.apple.com` を対象にした Google 検索を新規タブで開く**（Apple Music を直接叩くのではない）。

---

## 5. カテゴリ定義

`catLabels` / `catOrder` で定義。日別一覧はこの順で表示される。

| キー | ラベル |
|------|--------|
| `range` | 取得結果 |
| `thisweek` | 今週入荷 |
| `new` | 新入荷 |
| `preorder` | 予約受付中 |
| `used` | 中古盤 |

> `data.json` 側の通常マージでは `range` カテゴリは対象外（`fetchAndMerge` 内でスキップ）。`range` は設定ページの範囲取得専用。

---

## 6. 主な JavaScript 関数一覧

| 関数 | 役割 |
|------|------|
| `loadStorage` / `saveStorage` | localStorage の JSON 読み書き |
| `fetchAndMerge` | `data.json` を取得して差分マージ |
| `mergeRangeGroups` | 範囲取得結果を `range` として反映 |
| `buildTree` | `nt_data` から 年/月/日 ツリー生成 |
| `renderTree` | 左ツリーの描画 |
| `toggleYear` / `toggleMonth` | 年・月の開閉 |
| `selectDay` / `renderDay` | 日を選択・日別一覧を描画 |
| `showList` | スマホでリスト画面へ戻る |
| `toggle` | レコードのチェック切替（全チェックで削除確認） |
| `deleteDay` | 日ごと削除（削除記録付き） |
| `checkAll` / `uncheckAll` | 一括チェック / リセット |
| `updateProgress` / `updateCatCounts` / `updateDayBadge` | 各種カウント・進捗の更新 |
| `togglePool` / `removeFromPool` / `clearPool` / `renderPoolPage` | プール操作・描画 |
| `togglePoolPage` / `toggleSettingsPage` | ページの開閉 |
| `triggerRangeScrape` | 範囲取得ジョブの開始とポーリング |
| `openAppleMusic` | Apple Music を対象にした Google 検索を開く |
| `init` | 起動処理（取得→ツリー→最新日表示） |

---

## 7. 補足 / 注意点

- **Apple Music 検索の実装**: 現行は `openAppleMusic` のみ。かつて存在した iTunes Search API の JSONP 実装（`itunesJsonpSearch` / `normalizeAppleText` / `scoreAppleResult` / `pickBestAppleResult`。二重定義された未使用コード）は整理・削除済み。
- **外部依存**:
  - Google Fonts（`Courier Prime` / `Noto Sans JP`）
  - `data.json`（同ディレクトリに配置される想定）
  - Cloudflare Worker API（範囲取得。ドメインは `RANGE_API_BASE` にハードコード）
  - Apple Music 検索は Google 検索へのリンクのみで、iTunes API は現状未使用。
- **サーバー不要**: チェック状態・お気に入り・削除履歴はすべて端末の localStorage に保存されるため、ブラウザ/端末をまたぐと共有されない。
