# state-worker — チェック状態のサーバー保存

iOS Safari は、ホーム画面アプリを約1週間開かないと **localStorage を丸ごと削除**することがある。
このワーカーはチェック状態・お気に入り・削除履歴を Cloudflare KV に保存し、
データが消えても `index.html` を開くだけで自動復元できるようにするためのもの。

## デプロイ手順（一度だけ）

前提: Cloudflare アカウントがあること（範囲取得ワーカーで使っているアカウントでOK）。
`wrangler` が未インストールなら `npm install -g wrangler` 、初回は `wrangler login`。

1. このディレクトリに移動
   ```
   cd state-worker
   ```

2. KV 名前空間を作成
   ```
   wrangler kv namespace create NT_STATE
   ```
   → 表示された `id = "..."` の値を [wrangler.toml](wrangler.toml) の `id` に貼り付ける。

3. デプロイ
   ```
   wrangler deploy
   ```
   → 最後に表示される URL（例 `https://newtone-state.xxxx.workers.dev`）を控える。

4. 動作確認（任意）
   ```
   curl "https://newtone-state.xxxx.workers.dev/state?key=newtone-main"
   ```
   `{"ok":true,"data":null}` が返れば成功（まだ何も保存していない状態）。

## index.html 側の設定

デプロイで得た URL を `index.html` の定数に設定する:

```js
const SYNC_API_BASE = 'https://newtone-state.xxxx.workers.dev'; // ← ここに貼る
```

空文字のままだと同期はオフ（これまで通りローカル保存のみ）。URL を入れると次回起動から
自動でサーバーに保存・復元されるようになる。

## 補足

- 保存スロットは `SYNC_KEY`（既定 `newtone-main`）で分かれる。単一店舗ならこのままでよい。
- キーはソースに埋め込まれる＝サイトのソースを見れば分かるため、第三者が同じキーで
  読み書きできる点には留意（レコードのチェックリストなので影響は小さい）。より厳密にしたい
  場合は共有シークレットの付与などを追加できる。
