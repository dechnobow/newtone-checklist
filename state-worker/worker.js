// NEWTONE 新譜チェック — 状態保存用 Cloudflare Worker
//
// チェック状態・お気に入り・削除履歴などを KV に保存し、
// iOS Safari の localStorage 自動削除でデータが消えても復元できるようにする。
//
// エンドポイント:
//   GET  /state?key=<スロット名>   → { ok:true, data: <保存済みバンドル or null> }
//   PUT  /state?key=<スロット名>   → 本文(JSON)をそのまま保存し { ok:true }
//   POST /state?key=<スロット名>   → PUT と同じ
//
// 必要な設定: KV 名前空間を "NT_STATE" という名前でバインドする（wrangler.toml 参照）。

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,PUT,POST,OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { 'Content-Type': 'application/json', ...CORS },
  });
}

// 保存する JSON の上限（暴走・誤爆対策）。KV 値の上限は 25MB だが十分小さく制限しておく。
const MAX_BYTES = 5 * 1024 * 1024;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: CORS });
    }

    if (url.pathname !== '/state') {
      return json({ ok: false, error: 'not found' }, 404);
    }

    const key = url.searchParams.get('key');
    if (!key || key.length > 128) {
      return json({ ok: false, error: 'missing or invalid key' }, 400);
    }
    const kvKey = 'state:' + key;

    if (request.method === 'GET') {
      const val = await env.NT_STATE.get(kvKey);
      let data = null;
      if (val) {
        try { data = JSON.parse(val); } catch (_) { data = null; }
      }
      return json({ ok: true, data });
    }

    if (request.method === 'PUT' || request.method === 'POST') {
      const body = await request.text();
      if (body.length > MAX_BYTES) {
        return json({ ok: false, error: 'payload too large' }, 413);
      }
      // 妥当な JSON かだけ検証してから保存
      try { JSON.parse(body); } catch (_) {
        return json({ ok: false, error: 'invalid json' }, 400);
      }
      await env.NT_STATE.put(kvKey, body);
      return json({ ok: true });
    }

    return json({ ok: false, error: 'method not allowed' }, 405);
  },
};
