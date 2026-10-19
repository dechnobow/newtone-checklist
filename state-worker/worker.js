// NEWTONE 新譜チェック — 状態保存用 Cloudflare Worker (v2)
//
// v1 は「クライアントが送ってきた状態の塊をそのまま上書き保存」していたため、
// 中身の薄い端末（localStorage が消えた直後など）が一度 PUT するだけで
// サーバー側の履歴も消えた。
//
// v2 では状態を項目ごとの LWW（後勝ち）マップとして持ち、クライアントは
// 「この項目をオン／オフにした」という操作だけを送る。
//   - 送られていない項目は絶対に変わらない（丸ごと上書きの経路が存在しない）
//   - オフにする操作も時刻つきの記録として残る
//   - その日最初の書き込みの直前に、書き込み前の状態をスナップショット保存する
//
// 項目キー:  c:<id> チェック / p:<id> プール(値はレコード) /
//            ri:<id> 削除済みレコード / rd:<date> 削除済み日付
// 値:        { v: true | レコード | false | null, ts: ミリ秒 }
//
// エンドポイント（すべて ?key=<スロット名> 付き）:
//   GET  /state                 → { ok, meta, rev, updatedAt }
//   POST /ops     {ops:[...]}   → 操作を適用して { ok, applied, meta, rev }
//   GET  /snapshots             → { ok, dates:[...] }
//   GET  /snapshot?date=        → { ok, state }
//   POST /restore?date=         → スナップショットでオンだった項目のうち、
//                                 今オフ／欠けているものを今の時刻でオンに戻す
//   PUT/POST /state             → v1 クライアント互換。追加のみ反映し、何も消さない
//
// 必要な設定: KV 名前空間を "NT_STATE" という名前でバインドする（wrangler.toml 参照）。

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,PUT,POST,OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

const MAX_BYTES = 5 * 1024 * 1024;
const MAX_OPS = 2000;
const SNAPSHOT_TTL = 60 * 60 * 24 * 400; // 約13か月
const KEY_RE = /^(c|p|ri|rd):[^\s]{1,160}$/;

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { 'Content-Type': 'application/json', ...CORS },
  });
}

function todayJST() {
  return new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);
}

// v1 の塊を LWW マップに変換（オンの項目だけ。オフは表現されていないので作らない）
function legacyToMeta(b, meta, ts) {
  if (!b || typeof b !== 'object') return 0;
  let n = 0;
  const add = (k, v) => {
    if (!KEY_RE.test(k)) return;
    if (!meta[k]) { meta[k] = { v, ts }; n++; }
  };
  for (const [id, on] of Object.entries(b.checked || {})) if (on) add('c:' + id, true);
  for (const [id, r] of Object.entries(b.pool || {})) if (r && r.id) add('p:' + id, r);
  for (const [id, on] of Object.entries(b.removedIds || {})) if (on) add('ri:' + id, true);
  for (const [d, on] of Object.entries(b.removedDays || {})) if (on) add('rd:' + d, true);
  return n;
}

async function loadState(env, slot) {
  const raw = await env.NT_STATE.get('v2:' + slot);
  if (raw) {
    try { return JSON.parse(raw); } catch (_) { /* 壊れていたら v1 から作り直す */ }
  }
  const state = { meta: {}, rev: 0, updatedAt: 0 };
  const legacy = await env.NT_STATE.get('state:' + slot);
  if (legacy) {
    try { legacyToMeta(JSON.parse(legacy), state.meta, 1); } catch (_) {}
  }
  return state;
}

async function snapshotIfFirstToday(env, slot, state) {
  const snapKey = `snap:${slot}:${todayJST()}`;
  const exists = await env.NT_STATE.get(snapKey);
  if (!exists) {
    await env.NT_STATE.put(snapKey, JSON.stringify(state), { expirationTtl: SNAPSHOT_TTL });
  }
}

async function saveState(env, slot, state) {
  state.rev = (state.rev || 0) + 1;
  state.updatedAt = Date.now();
  await env.NT_STATE.put('v2:' + slot, JSON.stringify(state));
}

function applyOps(meta, ops) {
  let applied = 0;
  for (const op of ops) {
    if (!op || typeof op.k !== 'string' || !KEY_RE.test(op.k)) continue;
    if (typeof op.ts !== 'number' || !isFinite(op.ts)) continue;
    const cur = meta[op.k];
    if (!cur || op.ts > cur.ts) {
      meta[op.k] = { v: op.v === undefined ? null : op.v, ts: op.ts };
      applied++;
    }
  }
  return applied;
}

async function readJson(request) {
  const body = await request.text();
  if (body.length > MAX_BYTES) throw Object.assign(new Error('payload too large'), { status: 413 });
  try { return JSON.parse(body); } catch (_) {
    throw Object.assign(new Error('invalid json'), { status: 400 });
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === 'OPTIONS') return new Response(null, { headers: CORS });

    const slot = url.searchParams.get('key');
    if (!slot || slot.length > 128) return json({ ok: false, error: 'missing or invalid key' }, 400);

    try {
      // ── 現在の状態 ──
      if (url.pathname === '/state' && request.method === 'GET') {
        const s = await loadState(env, slot);
        return json({ ok: true, version: 2, meta: s.meta, rev: s.rev, updatedAt: s.updatedAt });
      }

      // ── 操作の適用（v2 クライアントの唯一の書き込み経路）──
      if (url.pathname === '/ops' && request.method === 'POST') {
        const body = await readJson(request);
        const ops = Array.isArray(body && body.ops) ? body.ops.slice(0, MAX_OPS) : [];
        const s = await loadState(env, slot);
        await snapshotIfFirstToday(env, slot, s);
        const applied = applyOps(s.meta, ops);
        if (applied > 0) await saveState(env, slot, s);
        return json({ ok: true, applied, meta: s.meta, rev: s.rev });
      }

      // ── v1 互換：追加のみ。何も消さない ──
      if (url.pathname === '/state' && (request.method === 'PUT' || request.method === 'POST')) {
        const body = await readJson(request);
        const s = await loadState(env, slot);
        await snapshotIfFirstToday(env, slot, s);
        const added = legacyToMeta(body, s.meta, 1);
        if (added > 0) await saveState(env, slot, s);
        return json({ ok: true, added });
      }

      // ── スナップショット一覧 ──
      if (url.pathname === '/snapshots' && request.method === 'GET') {
        const prefix = `snap:${slot}:`;
        const dates = [];
        let cursor;
        do {
          const page = await env.NT_STATE.list({ prefix, cursor });
          page.keys.forEach(k => dates.push(k.name.slice(prefix.length)));
          cursor = page.list_complete ? null : page.cursor;
        } while (cursor);
        dates.sort().reverse();
        return json({ ok: true, dates });
      }

      // ── スナップショット1件 ──
      if (url.pathname === '/snapshot' && request.method === 'GET') {
        const date = url.searchParams.get('date') || '';
        const raw = await env.NT_STATE.get(`snap:${slot}:${date}`);
        if (!raw) return json({ ok: false, error: 'snapshot not found' }, 404);
        return json({ ok: true, state: JSON.parse(raw) });
      }

      // ── スナップショットから復元（オンだったものを今の時刻でオンに戻す）──
      if (url.pathname === '/restore' && request.method === 'POST') {
        const date = url.searchParams.get('date') || '';
        const raw = await env.NT_STATE.get(`snap:${slot}:${date}`);
        if (!raw) return json({ ok: false, error: 'snapshot not found' }, 404);
        const snap = JSON.parse(raw);
        const s = await loadState(env, slot);
        await snapshotIfFirstToday(env, slot, s);
        const ts = Date.now();
        let restored = 0;
        for (const [k, e] of Object.entries((snap && snap.meta) || {})) {
          if (!e || !e.v) continue;
          const cur = s.meta[k];
          if (!cur || !cur.v) { s.meta[k] = { v: e.v, ts }; restored++; }
        }
        if (restored > 0) await saveState(env, slot, s);
        return json({ ok: true, restored, meta: s.meta, rev: s.rev });
      }

      return json({ ok: false, error: 'not found' }, 404);
    } catch (e) {
      return json({ ok: false, error: e.message || 'error' }, e.status || 500);
    }
  },
};
