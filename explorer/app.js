// Event Data Explorer — 元データ（加工なし）を「メニュー型ノード」で辿る事後分析ビューア
'use strict';

// ============================================================
// 1. データと索引
// ============================================================
const PK = {
  users: 'id', booths: 'id', categories: 'id', awards: 'id', check_ins: 'id', booth_ratings: 'id',
  award_votes: 'id', bingo_cards: 'id', bingo_cells: 'id', card_unlock_events: 'id', recommendation_scores: 'id',
  user_survey_answers: 'id', booth_tags: 'id', gacha_coin_uses: 'id', events: 'id', survey_questions: 'id', audit_logs: 'id', organizers: 'id', exhibitor_invites: 'booth_id', award_settings: 'event_id', gacha_settings: 'event_id', event_app_access: 'event_id', booth_categories: 'booth_id', exhibitor_booths: 'booth_id',
};
const FK = {
  booths: { category_id: 'categories' },
  check_ins: { user_id: 'users', booth_id: 'booths', cell_id: 'bingo_cells' },
  booth_ratings: { checkin_id: 'check_ins', user_id: 'users', booth_id: 'booths' },
  award_votes: { award_id: 'awards', user_id: 'users', booth_id: 'booths' },
  bingo_cards: { user_id: 'users' },
  bingo_cells: { card_id: 'bingo_cards', booth_id: 'booths', user_id: 'users' },
  card_unlock_events: { card_id: 'bingo_cards', user_id: 'users' },
  recommendation_scores: { unlock_event_id: 'card_unlock_events', user_id: 'users', booth_id: 'booths' },
  user_survey_answers: { user_id: 'users' },
  booth_tags: { booth_id: 'booths' },
  gacha_coin_uses: { user_id: 'users' },
  booth_categories: { booth_id: 'booths', category_id: 'categories' },
  exhibitor_booths: { user_id: 'users', booth_id: 'booths' },
};
const T = {}, BY = {}, REV = {};
let META = { tables: {} };

const rev = (target, id, table, fk) =>
  (REV[target]?.get(id) || []).filter((x) => x.table === table && (!fk || x.fk === fk)).map((x) => x.row);
const has = (t) => !!T[t];

async function load() {
  META = await fetch('data/meta.json').then((r) => r.json());
  const names = Object.keys(PK).filter((t) => META.tables[t]);
  let done = 0;
  await Promise.all(names.map(async (t) => {
    T[t] = await fetch(`data/${t}.json`).then((r) => r.json());
    $('#meta').textContent = `読み込み中… ${++done}/${names.length}`;
  }));
  for (const t of names) {
    BY[t] = new Map(T[t].map((r) => [r[PK[t]], r]));
    for (const r of T[t]) for (const [fk, target] of Object.entries(FK[t] || {})) {
      const v = r[fk]; if (v == null) continue;
      const m = (REV[target] ??= new Map());
      if (!m.has(v)) m.set(v, []);
      m.get(v).push({ table: t, fk, row: r });
    }
  }
  const total = names.reduce((a, t) => a + T[t].length, 0);
  $('#meta').innerHTML = `${total.toLocaleString()} 行 / ${names.length} テーブル ・ ${esc(META.source.split('/').slice(-2).join('/'))}`;
  filterState = defaultFilters(type); renderMode(); renderTypes(); renderSorts(); renderFilters(); renderEvent(); historyButtons(); refreshList(); showTab('summary');
}

// ============================================================
// 2. 表示用の言葉（値は変えず、読める日本語を添えるだけ）
// ============================================================
const JA = {
  teens: '10代', twenties: '20代', thirties: '30代', forties: '40代', fifties_plus: '50代以上',
  student: '学生', planner: '企画', teacher: '教員', designer: 'デザイナー', engineer: 'エンジニア', staff: '職員', other: 'その他',
  male: '男性', female: '女性', prefer_not_to_say: '無回答',
  internal: '学内', external: '学外', high: '高', mid: '中', low: '低',
  participant: '参加者', exhibitor: '出展者', manager: '運営', viewer: '閲覧者',
  RECOMMEND: '推薦', FREE_VISIT: '自由訪問', PRESURVEY: '事前アンケート',
};
const ja = (v) => (v == null ? '—' : JA[v] ?? v);
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const short = (id) => String(id ?? '').slice(0, 6);
const trunc = (s, n) => (s && s.length > n ? s.slice(0, n) + '…' : s || '');
const hhmm = (iso) => (iso ? iso.slice(11, 16) : '');
const num = (v) => (v == null ? 0 : Number(v));

// ============================================================
// 3. エンティティ（ユーザー・ブース・アワード・カテゴリ）
// ============================================================
function survey(uid) {
  const r = rev('users', uid, 'user_survey_answers')[0];
  if (!r) return null;
  let a = {};
  try { a = JSON.parse(r.custom_answers || '{}'); } catch { /* 生のまま扱う */ }
  return { ...a, row: r };
}
const questionText = (key) => (T.survey_questions || []).find((q) => q.question_key === key)?.question_text || key;
const participants = () => (T.users || []).filter((u) => u.role === 'participant');
const catName = (id) => BY.categories?.get(id)?.name ?? short(id);
const awardName = (id) => BY.awards?.get(id)?.name ?? short(id);
const boothName = (id) => { const b = BY.booths?.get(id); return b ? `#${b.display_code} ${b.name}` : short(id); };
const userCheckins = (uid) => rev('users', uid, 'check_ins').sort((a, b) => num(a.visit_order) - num(b.visit_order));
const boothCheckins = (bid) => rev('booths', bid, 'check_ins').sort((a, b) => (a.checked_in_at < b.checked_in_at ? -1 : 1));
const ratingOf = (ci) => rev('check_ins', ci.id, 'booth_ratings')[0];
const userCard = (uid) => rev('users', uid, 'bingo_cards')[0];
const assigned = (rows) => rows.filter((r) => r.was_assigned === '1');
const strategyOf = (r) => { try { return JSON.parse(r.reason_payload || '{}').strategy ?? '不明'; } catch { return '不明'; } };
// 推薦されたブースを、同じユーザーが（時刻を問わず）チェックインしているか。時刻は列ごとに JST/UTC が混ざるので比較しない
const recVisited = (r) => rev('users', r.user_id, 'check_ins').some((c) => c.booth_id === r.booth_id);
const hasComment = (r) => r.comment != null && String(r.comment).trim() !== '';
const exhibitorBooths = (uid) => rev('users', uid, 'exhibitor_booths').map((x) => x.booth_id);

function userTitle(uid) {
  const u = BY.users.get(uid); const s = survey(uid);
  const attrs = s ? [ja(s.age_range), ja(Array.isArray(s.occupation) ? s.occupation[0] : s.occupation), ja(s.affiliation)].join('・') : '';
  return { title: `${ja(u?.role)} ${u?.display_name ? trunc(u.display_name, 12) : short(uid)}`, sub: attrs };
}

const ENTITY = {
  users: {
    label: 'ユーザー', unit: '人', color: '#2563eb', shape: 'ellipse', icon: '●',
    title: (r) => userTitle(r.id).title,
    sub: (r) => { const s = userTitle(r.id).sub; const n = userCheckins(r.id).length; return [s, n ? `${n}ブース訪問` : ''].filter(Boolean).join('\n'); },
    visits: (r) => rev('users', r.id, 'check_ins').length,
    metrics: {
      visits: ['訪問数', (r) => rev('users', r.id, 'check_ins').length],
      ratings: ['評価数', (r) => rev('users', r.id, 'booth_ratings').length],
      comments: ['コメント数', (r) => rev('users', r.id, 'booth_ratings').filter(hasComment).length],
      recs: ['推薦された数', (r) => assigned(rev('users', r.id, 'recommendation_scores')).length],
      votes: ['投票数', (r) => rev('users', r.id, 'award_votes').length],
      gacha: ['ガチャ使用数', (r) => rev('users', r.id, 'gacha_coin_uses').length],
    },
    flags: {
      visited: ['チェックインした', (r) => rev('users', r.id, 'check_ins').length > 0],
      novisit: ['チェックインしていない', (r) => rev('users', r.id, 'check_ins').length === 0],
      recommended: ['推薦を受けた', (r) => assigned(rev('users', r.id, 'recommendation_scores')).length > 0],
      rec_visited: ['推薦先を訪問した', (r) => assigned(rev('users', r.id, 'recommendation_scores')).some(recVisited)],
      rated: ['評価した', (r) => rev('users', r.id, 'booth_ratings').length > 0],
      commented: ['コメントした', (r) => rev('users', r.id, 'booth_ratings').some(hasComment)],
      voted: ['アワードに投票した', (r) => rev('users', r.id, 'award_votes').length > 0],
      gacha: ['ガチャを使った', (r) => rev('users', r.id, 'gacha_coin_uses').length > 0],
      survey: ['アンケート回答済み', (r) => !!survey(r.id)],
      exhibitor: ['出展者として登録', (r) => exhibitorBooths(r.id).length > 0],
    },
    search: (r) => { const s = survey(r.id); return [r.id, r.display_name, r.google_id, r.role, ja(r.role), s && JSON.stringify(s.row)].join(' '); },
    filters: () => {
      const vals = (key) => [...new Set(T.user_survey_answers?.flatMap((r) => { const s = survey(r.user_id); const v = s?.[key]; return Array.isArray(v) ? v : v == null ? [] : [v]; }))].sort();
      return [
        { key: 'role', label: '役割', values: [...new Set(T.users.map((u) => u.role))], get: (r) => [r.role] },
        ...['age_range', 'occupation', 'affiliation', 'gender', 'exploration_disposition'].map((k) => ({
          key: k, label: { age_range: '年代', occupation: '職業', affiliation: '所属', gender: '性別', exploration_disposition: '探索傾向' }[k],
          values: vals(k), get: (r) => { const v = survey(r.id)?.[k]; return Array.isArray(v) ? v : [v]; } })),
        { key: 'interest', label: '関心カテゴリ', values: (T.categories || []).map((c) => c.id), show: catName,
          get: (r) => survey(r.id)?.interest_categories || [] },
      ];
    },
  },
  booths: {
    label: 'ブース', unit: '件', color: '#ea580c', shape: 'round-rectangle', icon: '■',
    title: (r) => `#${r.display_code} ${trunc(r.name, 16)}`,
    sub: (r) => `${catName(r.category_id)}\n${boothCheckins(r.id).length}人訪問`,
    visits: (r) => rev('booths', r.id, 'check_ins').length,
    search: (r) => [r.id, r.name, r.display_code, catName(r.category_id), r.description].join(' '),
    metrics: {
      visits: ['訪問数', (r) => rev('booths', r.id, 'check_ins').length],
      ratings: ['評価数', (r) => rev('booths', r.id, 'booth_ratings').length],
      avg: ['平均★', (r) => { const x = rev('booths', r.id, 'booth_ratings'); return x.length ? x.reduce((a, b) => a + num(b.rating), 0) / x.length : 0; }],
      comments: ['コメント数', (r) => rev('booths', r.id, 'booth_ratings').filter(hasComment).length],
      votes: ['得票数', (r) => rev('booths', r.id, 'award_votes').length],
      recs: ['推薦された数', (r) => assigned(rev('booths', r.id, 'recommendation_scores')).length],
    },
    flags: {
      commented: ['コメントあり', (r) => rev('booths', r.id, 'booth_ratings').some(hasComment)],
      voted: ['得票あり', (r) => rev('booths', r.id, 'award_votes').length > 0],
      novote: ['得票なし', (r) => rev('booths', r.id, 'award_votes').length === 0],
      recommended: ['推薦されたことがある', (r) => assigned(rev('booths', r.id, 'recommendation_scores')).length > 0],
      inactive: ['非公開 (is_active=0)', (r) => r.is_active === '0'],
    },
    filters: () => [{ key: 'category', label: 'カテゴリ', values: [...new Set(T.booths.map((b) => b.category_id))], show: catName, get: (r) => [r.category_id] }],
  },
  awards: {
    label: 'アワード', unit: '件', color: '#c026d3', shape: 'star', icon: '★',
    title: (r) => r.name, sub: (r) => `${rev('awards', r.id, 'award_votes').length}票`,
    visits: (r) => rev('awards', r.id, 'award_votes').length,
    search: (r) => [r.id, r.name].join(' '), filters: () => [],
  },
  events: {
    label: 'イベント', unit: '件', color: '#111827', shape: 'diamond', icon: '◆',
    title: (r) => r.name,
    sub: () => `参加者 ${participants().length}人 ・ チェックイン ${T.check_ins?.length ?? 0}件`,
    visits: () => T.check_ins?.length ?? 0,
    search: (r) => [r.id, r.name, r.venue].join(' '), filters: () => [],
  },
  categories: {
    label: 'カテゴリ', unit: '件', color: '#a16207', shape: 'hexagon', icon: '⬢',
    title: (r) => r.name, sub: (r) => `${rev('categories', r.id, 'booths').length}ブース`,
    visits: (r) => rev('categories', r.id, 'booths').reduce((a, b) => a + rev('booths', b.id, 'check_ins').length, 0),
    search: (r) => [r.id, r.name].join(' '), filters: () => [],
  },
};

// ============================================================
// 4. メニュー（ノード版プルダウン）の定義
//    item: {entity:[table,id], edge:'辺の文字', raw:[[table,row]...]} | {info, raw} | {bucket, items} | {cell:row}
// ============================================================
const visitEdge = (ci) => { const r = ratingOf(ci); return `${ci.visit_order}番目 ${hhmm(ci.checked_in_at)}${r ? ` ★${r.rating}` : ''}`; };
const ciRaw = (ci) => [['check_ins', ci], ...(ratingOf(ci) ? [['booth_ratings', ratingOf(ci)]] : [])];
const missing = (what, how) => [{ info: `${what}は\nまだ取得していません`, note: how }];
const stars = (rows) => [4, 3, 2, 1].map((s) => ({ s, rows: rows.filter((x) => num(x.r.rating) === s) })).filter((g) => g.rows.length);

const FACETS = {
  users: [
    { key: 'visits', title: '訪問したブース', items: (u) => userCheckins(u.id).map((ci) => ({ entity: ['booths', ci.booth_id], edge: visitEdge(ci), raw: ciRaw(ci) })) },
    { key: 'bingo', title: 'ビンゴカード', layout: 'grid', items: (u) => {
      const card = userCard(u.id); if (!card) return [];
      return rev('bingo_cards', card.id, 'bingo_cells').sort((a, b) => num(a.position) - num(b.position)).map((c) => ({ cell: c }));
    } },
    { key: 'votes', title: 'アワード投票先', items: (u) => rev('users', u.id, 'award_votes').map((v) => ({ entity: ['booths', v.booth_id], edge: awardName(v.award_id), raw: [['award_votes', v]] })) },
    { key: 'gacha', title: 'ガチャコイン', items: (u) => has('gacha_coin_uses')
      ? rev('users', u.id, 'gacha_coin_uses').map((g) => ({ info: `コイン${g.coin_index} 使用\n${hhmm(g.used_at)}`, raw: [['gacha_coin_uses', g]] }))
      : missing('ガチャコインの使用履歴', 'DB の gacha_coin_uses はダンプに含まれていません。獲得枚数は DB に保存されず、ビンゴのライン数から計算される値です。') },
    { key: 'survey', title: 'アンケート回答', items: (u) => {
      const s = survey(u.id); if (!s) return [];
      const out = [];
      for (const [k, v] of Object.entries(s)) {
        if (k === 'row') continue;
        if (k === 'interest_categories') v.forEach((c) => out.push({ entity: ['categories', c], edge: '関心あり', raw: [['user_survey_answers', s.row]] }));
        else if (k === 'top_interest_category') out.push({ entity: ['categories', v], edge: '一番の関心', raw: [['user_survey_answers', s.row]] });
        else out.push({ info: `${questionText(k)}\n${[].concat(v).map(ja).join('・')}`, raw: [['user_survey_answers', s.row]] });
      }
      return out;
    } },
    { key: 'recs', title: '推薦されたブース', items: (u) => rev('users', u.id, 'recommendation_scores').filter((r) => r.was_assigned === '1')
      .sort((a, b) => (a.created_at < b.created_at ? -1 : 1))
      .map((r) => ({ entity: ['booths', r.booth_id], edge: `推薦 ${hhmm(r.created_at)} ${strategyOf(r)} ${recVisited(r) ? '→ 訪問した' : '→ 未訪問'}`, raw: [['recommendation_scores', r]] })) },
    { key: 'exhibit', title: '担当ブース（出展者）', items: (u) => rev('users', u.id, 'exhibitor_booths').map((x) => ({ entity: ['booths', x.booth_id], edge: '出展', raw: [['exhibitor_booths', x]] })) },
    { key: 'unlocks', title: 'カード解放の履歴', items: (u) => (userCard(u.id) ? rev('bingo_cards', userCard(u.id).id, 'card_unlock_events') : []).sort((a, b) => (a.created_at < b.created_at ? -1 : 1))
      .map((e) => ({ info: `${hhmm(e.created_at)} ${e.phase}\n${ja(e.strategy)}`, raw: [['card_unlock_events', e]] })) },
  ],
  booths: [
    { key: 'visitors', title: '訪問したユーザー', items: (b) => boothCheckins(b.id).map((ci) => ({ entity: ['users', ci.user_id], edge: `${hhmm(ci.checked_in_at)}${ratingOf(ci) ? ` ★${ratingOf(ci).rating}` : ''}`, raw: ciRaw(ci) })) },
    { key: 'ratings', title: '評価', items: (b) => stars(rev('booths', b.id, 'booth_ratings').map((r) => ({ r }))).map((g) => ({
      bucket: `★${g.s}  ${g.rows.length}人`, items: g.rows.map(({ r }) => ({ entity: ['users', r.user_id], edge: `★${r.rating}`, raw: [['booth_ratings', r]] })) })) },
    { key: 'comments', title: 'コメント', items: (b) => {
      const rows = rev('booths', b.id, 'booth_ratings').filter((r) => r.comment);
      if (!T.booth_ratings?.length || !('comment' in T.booth_ratings[0])) return missing('コメント', 'DB の booth_ratings.comment 列はダンプに含まれていません。');
      return rows.map((r) => ({ info: `★${r.rating} ${trunc(r.comment, 40)}`, raw: [['booth_ratings', r]] }));
    } },
    { key: 'stats', title: '件数', items: (b) => {
      const ci = rev('booths', b.id, 'check_ins'); const rt = rev('booths', b.id, 'booth_ratings');
      return [
        { info: `チェックイン ${ci.length}件\n(ユニーク ${new Set(ci.map((c) => c.user_id)).size}人)` },
        { info: `評価 ${rt.length}件` },
        { info: `アワード票 ${rev('booths', b.id, 'award_votes').length}票` },
        { info: `ビンゴに出現 ${rev('booths', b.id, 'bingo_cells').length}マス` },
        { info: `推薦された ${rev('booths', b.id, 'recommendation_scores').filter((r) => r.was_assigned === '1').length}回` },
      ];
    } },
    { key: 'awards', title: 'アワード得票', items: (b) => {
      const by = {};
      for (const v of rev('booths', b.id, 'award_votes')) (by[v.award_id] ??= []).push(v);
      return Object.entries(by).sort((a, b2) => b2[1].length - a[1].length).map(([aid, vs]) => ({
        bucket: `${awardName(aid)}  ${vs.length}票`, items: vs.map((v) => ({ entity: ['users', v.user_id], edge: '投票', raw: [['award_votes', v]] })) }));
    } },
    { key: 'recommended', title: '推薦された相手', items: (b) => rev('booths', b.id, 'recommendation_scores').filter((r) => r.was_assigned === '1')
      .map((r) => ({ entity: ['users', r.user_id], edge: `推薦 ${hhmm(r.created_at)} ${recVisited(r) ? '→ 訪問した' : '→ 未訪問'}`, raw: [['recommendation_scores', r]] })) },
    { key: 'category', title: 'カテゴリ', items: (b) => [{ entity: ['categories', b.category_id], edge: '所属', raw: [['booths', b]] }] },
    { key: 'exhibitors', title: '出展者', items: (b) => rev('booths', b.id, 'exhibitor_booths').map((x) => ({ entity: ['users', x.user_id], edge: '出展', raw: [['exhibitor_booths', x]] })) },
    { key: 'desc', title: '説明', items: (b) => (b.description ? [{ info: trunc(b.description, 120), raw: [['booths', b]] }] : []) },
  ],
  awards: [
    { key: 'booths', title: '得票したブース', items: (a) => {
      const by = {};
      for (const v of rev('awards', a.id, 'award_votes')) (by[v.booth_id] ??= []).push(v);
      return Object.entries(by).sort((x, y) => y[1].length - x[1].length).map(([bid, vs]) => ({ entity: ['booths', bid], edge: `${vs.length}票`, raw: vs.map((v) => ['award_votes', v]) }));
    } },
  ],
  categories: [
    { key: 'booths', title: 'ブース', items: (c) => rev('categories', c.id, 'booths').map((b) => ({ entity: ['booths', b.id], edge: '', raw: [['booths', b]] })) },
    { key: 'fans', title: '関心ありと答えた人', items: (c) => (T.user_survey_answers || []).filter((r) => survey(r.user_id)?.interest_categories?.includes(c.id))
      .map((r) => ({ entity: ['users', r.user_id], edge: '関心あり', raw: [['user_survey_answers', r]] })) },
  ],
};
FACETS.events = [
  { key: 'numbers', title: 'イベントの数字', items: () => {
    const P = participants(); const ci = T.check_ins || []; const rt = T.booth_ratings || [];
    const recs = assigned(T.recommendation_scores || []); const recV = recs.filter(recVisited);
    const pv = P.filter((u) => rev('users', u.id, 'check_ins').length).length;
    return [
      { info: `参加者 ${P.length}人\n（全ユーザー ${T.users?.length ?? 0}人）` },
      { info: `チェックインした参加者\n${pv}人（${pct(pv, P.length)}）` },
      { info: `チェックイン ${ci.length}件\n1人あたり ${fmtN(pv ? ci.length / pv : 0)}件` },
      { info: `評価 ${rt.length}件\nコメント ${rt.filter(hasComment).length}件` },
      { info: `推薦された枠 ${recs.length}\n推薦先を訪問 ${recV.length}（${pct(recV.length, recs.length)}）` },
      { info: `アワード投票 ${T.award_votes?.length ?? 0}票\n投票した人 ${new Set((T.award_votes || []).map((v) => v.user_id)).size}人` },
      { info: `ガチャコイン使用 ${T.gacha_coin_uses?.length ?? 0}枚\n使った人 ${new Set((T.gacha_coin_uses || []).map((g) => g.user_id)).size}人` },
      { info: `ビンゴカード ${T.bingo_cards?.length ?? 0}枚\n達成マス ${(T.bingo_cells || []).filter((c) => c.is_achieved === '1').length}` },
      { info: `アンケート回答 ${T.user_survey_answers?.length ?? 0}人` },
      { info: `ブース ${T.booths?.length ?? 0} ・ 出展者 ${new Set((T.exhibitor_booths || []).map((x) => x.user_id)).size}人` },
    ];
  } },
  { key: 'people', title: 'ユーザー（役割別）', items: () => {
    const by = {}; for (const u of T.users || []) (by[u.role] ??= []).push(u);
    return Object.entries(by).sort((a, b) => b[1].length - a[1].length).map(([role, us]) => ({
      bucket: `${ja(role)}  ${us.length}人`, items: us.map((u) => ({ entity: ['users', u.id], edge: '' })) }));
  } },
  { key: 'booths', title: 'ブース（カテゴリ別）', items: () => {
    const by = {}; for (const b of T.booths || []) (by[b.category_id] ??= []).push(b);
    return Object.entries(by).map(([cid, bs]) => ({ bucket: `${catName(cid)}  ${bs.length}件`, items: bs.map((b) => ({ entity: ['booths', b.id], edge: '' })) }));
  } },
  { key: 'awards', title: 'アワード', items: () => (T.awards || []).map((a) => ({ entity: ['awards', a.id], edge: `${rev('awards', a.id, 'award_votes').length}票` })) },
  { key: 'categories', title: 'カテゴリ', items: () => (T.categories || []).map((c) => ({ entity: ['categories', c.id], edge: `${rev('categories', c.id, 'booths').length}ブース` })) },
  { key: 'questions', title: 'アンケートの質問', items: () => (T.survey_questions || []).sort((a, b) => num(a.display_order) - num(b.display_order))
    .map((q) => ({ info: `${q.display_order}. ${trunc(q.question_text, 30)}`, raw: [['survey_questions', q]] })) },
  { key: 'settings', title: '設定', items: () => ['event_app_access', 'gacha_settings', 'award_settings'].flatMap((t) => (T[t] || []).map((r) => ({ info: t, raw: [[t, r]] }))) },
];
FACETS.bingo_cells = [{ key: 'booth', title: 'このマスのブース', items: (c) => (c.booth_id ? [{ entity: ['booths', c.booth_id], edge: `マス${c.position}`, raw: [['bingo_cells', c]] }] : []) }];

// ============================================================
// 5. キャンバス
// ============================================================
const cy = cytoscape({
  container: $('#cy'),
  userPanningEnabled: false,    // 1本指ドラッグは範囲選択に使う
  userZoomingEnabled: false,    // 2本指スワイプ/ピンチは下で自前処理
  boxSelectionEnabled: true,
  selectionType: 'additive',
  style: [
    { selector: 'node', style: {
      label: 'data(label)', 'font-size': 11, color: '#1f2328', 'text-wrap': 'wrap', 'text-max-width': 150,
      'text-valign': 'bottom', 'text-margin-y': 5, 'background-color': 'data(color)', 'border-width': 2, 'border-color': '#fff',
      'text-background-color': '#fff', 'text-background-opacity': 0.85, 'text-background-padding': 2, 'text-background-shape': 'roundrectangle' } },
    { selector: 'node.entity', style: { shape: 'data(shape)', width: 34, height: 34 } },
    { selector: 'node.entity.root', style: { width: 48, height: 48, 'font-size': 13, 'font-weight': 700, 'border-width': 4, 'border-color': '#111' } },
    { selector: 'node.entity.open', style: { 'border-color': '#111' } },
    { selector: 'node.facet', style: {
      shape: 'round-rectangle', width: 'label', height: 'label', padding: 7, 'background-color': '#fff', 'border-width': 1.5,
      'border-color': 'data(color)', 'text-valign': 'center', 'text-margin-y': 0, 'font-size': 11, 'font-weight': 600,
      color: '#1f2328', 'text-background-opacity': 0 } },
    { selector: 'node.facet.open', style: { 'background-color': 'data(color)', color: '#fff' } },
    { selector: 'node.facet.empty', style: { opacity: 0.45 } },
    { selector: 'node.bucket', style: {
      shape: 'round-tag', width: 'label', height: 'label', padding: 7, 'background-color': '#f8fafc', 'border-width': 1.5,
      'border-color': 'data(color)', 'border-style': 'dashed', 'text-valign': 'center', 'text-margin-y': 0, 'text-background-opacity': 0 } },
    { selector: 'node.bucket.open', style: { 'border-style': 'solid' } },
    { selector: 'node.info', style: {
      shape: 'rectangle', width: 'label', height: 'label', padding: 6, 'background-color': '#f1f5f9', 'border-width': 0,
      'text-valign': 'center', 'text-margin-y': 0, 'font-size': 10, color: '#334155', 'text-background-opacity': 0 } },
    { selector: 'node.info.missing', style: { 'background-color': '#fffbeb', 'border-width': 1, 'border-color': '#f59e0b', color: '#92400e' } },
    { selector: 'node.more', style: {
      shape: 'round-rectangle', width: 'label', height: 'label', padding: 5, 'background-color': '#fff', 'border-width': 1,
      'border-color': '#94a3b8', 'border-style': 'dotted', 'text-valign': 'center', 'text-margin-y': 0, color: '#475569', 'text-background-opacity': 0 } },
    { selector: 'node.cell', style: {
      shape: 'rectangle', width: 70, height: 56, 'background-color': '#ecfeff', 'border-width': 1, 'border-color': '#06b6d4',
      'text-valign': 'center', 'text-margin-y': 0, 'font-size': 9, 'text-max-width': 66, 'text-background-opacity': 0 } },
    { selector: 'node.cell.achieved', style: { 'background-color': '#06b6d4', color: '#fff' } },
    { selector: 'node.cluster', style: {
      shape: 'round-rectangle', 'background-color': 'data(color)', 'background-opacity': 0.08, 'border-width': 1.5,
      'border-color': 'data(color)', 'border-style': 'dashed', label: 'data(label)', 'text-valign': 'top', 'text-halign': 'center',
      'text-margin-y': -4, 'font-size': 13, 'font-weight': 700, padding: 24, 'text-background-opacity': 0 } },
    { selector: 'node:selected', style: { 'overlay-color': '#2563eb', 'overlay-opacity': 0.18, 'overlay-padding': 6 } },
    { selector: 'edge', style: {
      width: 1.2, 'line-color': '#cbd5e1', 'curve-style': 'bezier', 'target-arrow-shape': 'triangle', 'target-arrow-color': '#cbd5e1',
      'arrow-scale': 0.7, label: 'data(label)', 'font-size': 9, color: '#475569', 'text-rotation': 'autorotate',
      'text-background-color': '#fff', 'text-background-opacity': 0.9, 'text-background-padding': 1 } },
    { selector: 'edge.menu', style: { 'line-style': 'dashed', 'target-arrow-shape': 'none', 'line-color': '#e2e8f0' } },
    { selector: 'edge.hl', style: { 'line-color': '#2563eb', 'target-arrow-color': '#2563eb', width: 2.2 } },
    { selector: '.dim', style: { opacity: 0.12 } },
    { selector: 'node.anc', style: { 'underlay-color': '#f59e0b', 'underlay-opacity': 0.35, 'underlay-padding': 8 } },
    { selector: 'node.kid', style: { 'underlay-color': '#10b981', 'underlay-opacity': 0.3, 'underlay-padding': 6 } },
    { selector: '.hidden', style: { display: 'none' } },
  ],
});

// 2本指スワイプ = 移動、ピンチ（macOS では ctrl+wheel として届く）= 拡大縮小
$('#cy').addEventListener('wheel', (e) => {
  e.preventDefault(); e.stopPropagation();
  if (e.ctrlKey || e.metaKey) {
    const rect = $('#cy').getBoundingClientRect();
    const level = Math.min(4, Math.max(0.1, cy.zoom() * Math.exp(-e.deltaY * 0.01)));
    cy.zoom({ level, renderedPosition: { x: e.clientX - rect.left, y: e.clientY - rect.top } });
  } else {
    cy.panBy({ x: -e.deltaX, y: -e.deltaY });
  }
}, { passive: false, capture: true });
// スペースキーを押しながらドラッグでも移動できるように
let spaceDown = false;
document.addEventListener('keyup', (e) => { if (e.code === 'Space') { spaceDown = false; cy.userPanningEnabled(false); cy.boxSelectionEnabled(true); } });

// ---- ノードの追加 ----
let seq = 0;
const eid = (t, id) => `${t}:${id}`;
const center = () => { const e = cy.extent(); return { x: (e.x1 + e.x2) / 2, y: (e.y1 + e.y2) / 2 }; };

function entityLabel(t, r) { const E = ENTITY[t]; return E ? `${E.title(r)}\n${E.sub(r)}` : short(r[PK[t]]); }

function addEntity(t, id, { owner = null, pos } = {}) {
  const ex = cy.getElementById(eid(t, id));
  if (ex.nonempty()) return ex;
  const r = BY[t]?.get(id); if (!r) return null;
  const E = ENTITY[t];
  return cy.add({ group: 'nodes', classes: `entity${owner ? '' : ' root'}`, position: pos || center(),
    data: { id: eid(t, id), kind: 'entity', table: t, pk: id, owner, label: entityLabel(t, r), color: E?.color || '#64748b', shape: E?.shape || 'ellipse' } });
}

function addAux(kind, { owner, label, pos, color = '#64748b', extra = {}, classes = '' }) {
  return cy.add({ group: 'nodes', classes: `${kind} ${classes}`, position: pos,
    data: { id: `${kind}#${++seq}`, kind, owner, label, color, ...extra } });
}

function addEdge(src, tgt, { label = '', owner, raw, menu = false }) {
  const id = `e:${src.id()}>${tgt.id()}:${label}`;
  if (cy.getElementById(id).nonempty()) return;
  cy.add({ group: 'edges', classes: menu ? 'menu' : '', data: { id, source: src.id(), target: tgt.id(), label, owner, raw } });
}

// ---- 配置: 親から外向きに扇形に広げる ----
function outward(node) {
  const o = node.data('owner') && cy.getElementById(node.data('owner'));
  const p = node.position();
  if (o && o.nonempty()) { const q = o.position(); const dx = p.x - q.x, dy = p.y - q.y; const l = Math.hypot(dx, dy) || 1; return Math.atan2(dy / l, dx / l); }
  return -Math.PI / 2;
}
function fan(node, n, { spread, radius }) {
  const base = outward(node); const p = node.position();
  const arc = Math.min(spread, n > 1 ? spread : 0);
  const perRing = Math.max(6, Math.floor((radius * arc) / 70) + 1);
  return Array.from({ length: n }, (_, i) => {
    const ring = Math.floor(i / perRing), inRing = Math.min(perRing, n - ring * perRing), j = i % perRing;
    const a = base + (inRing > 1 ? -arc / 2 + (arc * j) / (inRing - 1) : 0);
    const r = radius + ring * 85;
    return { x: p.x + r * Math.cos(a), y: p.y + r * Math.sin(a) };
  });
}

// ---- メニューを開く/閉じる ----
function toggleMenu(node) {
  const facets = FACETS[node.data('table')]; if (!facets) return;
  if (node.hasClass('open')) { collapse(node); node.removeClass('open'); return; }
  const r = BY[node.data('table')].get(node.data('pk'));
  const isRoot = !node.data('owner');
  const pos = fan(node, facets.length, { spread: isRoot ? Math.PI * 2 * (1 - 1 / facets.length) : Math.PI * 0.9, radius: isRoot ? 210 : 150 });
  facets.forEach((f, i) => {
    const items = f.items(r);
    const fn = addAux('facet', { owner: node.id(), pos: pos[i], color: node.data('color'), classes: items.length ? '' : 'empty',
      label: `▸ ${f.title}  ${items.length}`, extra: { facet: f.key, title: f.title, items, layout: f.layout, shown: 0 } });
    addEdge(node, fn, { owner: node.id(), menu: true });
  });
  node.addClass('open');
  deoverlap(cy.nodes(`[owner="${node.id()}"]`));
  reveal(node.closedNeighborhood());
}

function toggleFacet(fn) {
  if (fn.hasClass('open')) { collapse(fn); fn.removeClass('open'); fn.data('shown', 0); fn.data('label', fn.data('label').replace('▾', '▸')); return; }
  fn.addClass('open'); fn.data('label', fn.data('label').replace('▸', '▾'));
  showMore(fn);
}

function showMore(fn) {
  cy.nodes(`[owner="${fn.id()}"].more`).remove();
  const items = fn.data('items'); const from = fn.data('shown');
  const limit = Number($('#limit').value);
  const slice = items.slice(from, from + limit);
  if (fn.data('layout') === 'grid') placeGrid(fn, slice);
  else {
    const pos = fan(fn, slice.length + (from + limit < items.length ? 1 : 0), { spread: Math.PI * 0.85, radius: 170 + Math.min(slice.length, 20) * 6 });
    slice.forEach((it, i) => placeItem(fn, it, pos[i]));
    if (from + limit < items.length) {
      const m = addAux('more', { owner: fn.id(), pos: pos[slice.length], label: `…残り ${items.length - from - limit} 件を表示` });
      addEdge(fn, m, { owner: fn.id(), menu: true });
    }
  }
  fn.data('shown', from + slice.length);
  deoverlap(cy.nodes(`[owner="${fn.id()}"]`));
  reveal(fn.closedNeighborhood());
  updateUI();
}

function placeItem(parent, it, pos) {
  const owner = parent.id();
  let n;
  if (it.entity) {
    n = addEntity(it.entity[0], it.entity[1], { owner, pos });
    if (!n) return;
  } else if (it.bucket) {
    n = addAux('bucket', { owner, pos, label: `▸ ${it.bucket}`, color: parent.data('color'), extra: { items: it.items, shown: 0, title: it.bucket } });
  } else if (it.cell) {
    return placeCell(parent, it.cell, pos);
  } else {
    n = addAux('info', { owner, pos, label: it.info, classes: it.note ? 'missing' : '', extra: { raw: it.raw, note: it.note } });
  }
  addEdge(parent, n, { owner, label: it.edge || '', raw: it.raw, menu: !it.entity });
}

function placeGrid(fn, cells) {
  const base = outward(fn); const p = fn.position();
  const c = { x: p.x + 300 * Math.cos(base), y: p.y + 300 * Math.sin(base) };
  const size = Math.ceil(Math.sqrt(cells.length)) || 1;
  cells.forEach((cell, i) => {
    const pos = num(cell.position) - 1 >= 0 ? num(cell.position) - 1 : i;
    const col = pos % size, row = Math.floor(pos / size);
    placeCell(fn, cell, { x: c.x + (col - (size - 1) / 2) * 78, y: c.y + (row - (size - 1) / 2) * 64 });
  });
}
function placeCell(parent, cell, pos) {
  const b = BY.booths?.get(cell.booth_id);
  const label = `マス${cell.position}${cell.is_achieved === '1' ? ' ✓' : ''}\n${b ? `#${b.display_code} ${trunc(b.name, 8)}` : '（未公開）'}\n${cell.source ? ja(cell.source) : ''}`;
  const n = cy.getElementById(eid('bingo_cells', cell.id)).nonempty() ? cy.getElementById(eid('bingo_cells', cell.id))
    : cy.add({ group: 'nodes', classes: `cell${cell.is_achieved === '1' ? ' achieved' : ''}`, position: pos,
      data: { id: eid('bingo_cells', cell.id), kind: 'entity', table: 'bingo_cells', pk: cell.id, owner: parent.id(), label, color: '#06b6d4' } });
  addEdge(parent, n, { owner: parent.id(), menu: true, raw: [['bingo_cells', cell]] });
}

function toggleBucket(bn) {
  if (bn.hasClass('open')) { collapse(bn); bn.removeClass('open'); bn.data('label', bn.data('label').replace('▾', '▸')); bn.data('shown', 0); return; }
  bn.addClass('open'); bn.data('label', bn.data('label').replace('▸', '▾'));
  showMore(bn);
}

// ---- 削除: そのノードから広げたものを全部消す（他から繋がっているユーザー/ブースは残す） ----
function subtree(node) {
  const out = new Set();
  const stack = [node.id()];
  while (stack.length) {
    const id = stack.pop();
    cy.nodes(`[owner="${id}"]`).forEach((c) => { if (!out.has(c.id())) { out.add(c.id()); stack.push(c.id()); } });
  }
  return out;
}
function cascade(node, includeSelf) {
  const S = subtree(node); if (includeSelf) S.add(node.id());
  // 消す範囲の外から辺が来ているエンティティは残す（その子孫ごと）
  const keep = new Set();
  for (const id of S) {
    const n = cy.getElementById(id);
    if (!n.hasClass('entity') || id === node.id()) continue;
    const outside = n.connectedEdges().filter((e) => { const o = e.source().id() === id ? e.target() : e.source(); return !S.has(o.id()) && o.id() !== node.id(); });
    if (outside.nonempty()) {
      keep.add(id); subtree(n).forEach((d) => keep.add(d));
      const o = outside[0].source().id() === id ? outside[0].target() : outside[0].source();
      n.data('owner', o.id());
    }
  }
  const del = [...S].filter((id) => !keep.has(id));
  cy.edges().filter((e) => S.has(e.data('owner')) && !keep.has(e.data('owner'))).remove();
  cy.collection(del.map((id) => cy.getElementById(id))).remove();
}
const collapse = (node) => { cascade(node, false); updateUI(); };
function removeNodes(nodes) {
  nodes.forEach((n) => {
    if (n.removed()) return;
    if (n.hasClass('cluster')) { n.children().move({ parent: null }); n.remove(); return; }
    const owner = n.data('owner') && cy.getElementById(n.data('owner'));
    cascade(n, true);
    if (owner && owner.nonempty() && cy.nodes(`[owner="${owner.id()}"]`).empty()) {
      owner.removeClass('open');
      if (owner.data('label')) owner.data('label', owner.data('label').replace('▾', '▸'));
      owner.data('shown', 0);
    }
  });
  dropEmptyClusters(); showDetail(null); updateUI();
}

// ---- 重なり解消: movable を、ラベル込みの外接矩形が他と重ならない位置まで押し出す ----
function deoverlap(movable = cy.nodes(), iters = 60) {
  const all = cy.nodes().filter((n) => !n.isParent() && n.visible());
  const mov = new Set(movable.filter((n) => !n.isParent() && n.visible()).map((n) => n.id()));
  if (!mov.size) return;
  const items = all.map((n) => ({ n, m: mov.has(n.id()), b: n.boundingBox({ includeLabels: true, includeOverlays: false }) }));
  const GAP = 10;
  for (let it = 0; it < iters; it++) {
    let moved = false;
    for (let i = 0; i < items.length; i++) {
      const a = items[i];
      for (let j = i + 1; j < items.length; j++) {
        const b = items[j];
        if (!a.m && !b.m) continue;
        const ox = Math.min(a.b.x2, b.b.x2) - Math.max(a.b.x1, b.b.x1) + GAP;
        const oy = Math.min(a.b.y2, b.b.y2) - Math.max(a.b.y1, b.b.y1) + GAP;
        if (ox <= 0 || oy <= 0) continue;
        moved = true;
        let dx = (a.b.x1 + a.b.x2) - (b.b.x1 + b.b.x2), dy = (a.b.y1 + a.b.y2) - (b.b.y1 + b.b.y2);
        if (!dx && !dy) { dx = i % 2 ? 1 : -1; dy = 0.5; }
        const share = a.m && b.m ? 0.5 : 1;
        const d = ox < oy ? { x: Math.sign(dx || 1) * ox * share, y: 0 } : { x: 0, y: Math.sign(dy || 1) * oy * share };
        const move = (o, k) => { o.n.shift({ x: d.x * k, y: d.y * k }); o.b = { x1: o.b.x1 + d.x * k, x2: o.b.x2 + d.x * k, y1: o.b.y1 + d.y * k, y2: o.b.y2 + d.y * k }; };
        if (a.m) move(a, 1);
        if (b.m) move(b, -1);
      }
    }
    if (!moved) break;
  }
}

// ---- 視野 ----
function reveal(eles) {
  const bb = eles.boundingBox(); const e = cy.extent();
  if (bb.x1 < e.x1 || bb.x2 > e.x2 || bb.y1 < e.y1 || bb.y2 > e.y2) {
    cy.animate({ fit: { eles: cy.elements(':visible').union(eles), padding: 50 } }, { duration: 300 });
  }
}

// ---- 元に戻す / やり直す: 操作の直前にキャンバス全体を保存 ----
const undoStack = [], redoStack = [];
const HISTORY_MAX = 80;
function capture() {
  const els = cy.elements().jsons();
  return els.sort((a, b) => (a.group === b.group ? (a.classes?.includes('cluster') ? -1 : b.classes?.includes('cluster') ? 1 : 0) : a.group === 'nodes' ? -1 : 1));
}
function snapshot() {
  undoStack.push({ els: capture(), seq });
  if (undoStack.length > HISTORY_MAX) undoStack.shift();
  redoStack.length = 0; historyButtons();
}
function restoreFrom(from, to) {
  if (!from.length) return;
  to.push({ els: capture(), seq });
  const st = from.pop();
  cy.elements().remove(); cy.add(st.els); seq = Math.max(seq, st.seq);
  cy.$(':selected').unselect(); showDetail(null); historyButtons(); updateUI();
}
const undo = () => restoreFrom(undoStack, redoStack);
const redo = () => restoreFrom(redoStack, undoStack);
function historyButtons() {
  $('#btn-undo').disabled = !undoStack.length; $('#btn-redo').disabled = !redoStack.length;
  $('#btn-undo').title = `元に戻す (⌘Z) ・ ${undoStack.length}手`;
}

// ============================================================
// 6. 操作
// ============================================================
cy.on('tap', 'node', (e) => {
  const n = e.target;
  if (!n.hasClass('info') && !n.hasClass('cluster') && !(n.hasClass('facet') && !n.data('items').length)) snapshot();
  if (n.hasClass('entity')) toggleMenu(n);
  else if (n.hasClass('facet')) { if (n.data('items').length) toggleFacet(n); }
  else if (n.hasClass('bucket')) toggleBucket(n);
  else if (n.hasClass('more')) { const p = cy.getElementById(n.data('owner')); n.remove(); showMore(p); }
  showDetail(n.removed() ? null : n);
  if (!n.hasClass('info') && !n.hasClass('cluster')) autoArrange();
  updateUI();
});
cy.on('tap', 'edge', (e) => showEdge(e.target));
cy.on('tap', (e) => { if (e.target === cy) { cy.$(':selected').unselect(); showDetail(null); } });
// ---- ホバー: 親の流れ（起点まで）と子を強調し、親との関係をポップアップと右パネルに ----
const ownerOf = (n) => (n.data('owner') ? cy.getElementById(n.data('owner')) : cy.collection());
function lineage(n) {   // 自分 → … → 起点
  const out = [n]; let c = ownerOf(n);
  while (c.nonempty() && out.length < 30) { out.push(c); c = ownerOf(c); }
  return out;
}
function parentEntity(n) {   // メニュー/まとまりを飛ばして、一番近い親のエンティティ（とその間のメニュー名）
  const via = []; let c = ownerOf(n);
  while (c.nonempty()) { if (c.hasClass('entity')) return { p: c, via }; via.unshift(c.data('title') || ''); c = ownerOf(c); }
  return null;
}
cy.on('mouseover', 'node', (e) => {
  const n = e.target; if (n.hasClass('cluster')) return;
  const line = cy.collection(lineage(n));
  const kids = cy.nodes(`[owner="${n.id()}"]`);
  const focus = line.union(kids).union(n.connectedEdges());
  const lineEdges = line.edgesWith(line).union(n.edgesWith(kids));
  cy.elements().not(focus).not(lineEdges).not('.cluster').addClass('dim');
  lineEdges.addClass('hl'); line.not(n).addClass('anc'); kids.addClass('kid');
  showPop(n, e.renderedPosition || n.renderedPosition());
  showRelation(n);
});
cy.on('mouseout', 'node', () => { cy.elements().removeClass('dim hl anc kid'); $('#pop').style.display = 'none'; });
cy.on('pan zoom', () => { $('#pop').style.display = 'none'; });

function showPop(n, rp) {
  const pe = parentEntity(n);
  const kids = cy.nodes(`[owner="${n.id()}"]`);
  const kidCount = {}; kids.forEach((k) => { const t = k.hasClass('entity') ? (ENTITY[k.data('table')]?.label || k.data('table')) : k.hasClass('facet') ? 'メニュー' : k.hasClass('bucket') ? 'まとまり' : '値'; kidCount[t] = (kidCount[t] || 0) + 1; });
  const title = (n.data('label') || '').split('\n')[0];
  let rel = '';
  if (pe && n.hasClass('entity')) rel = relationLines(pe.p, n).slice(0, 6).map((l) => `<div>${l}</div>`).join('');
  $('#pop').innerHTML = `<div class="pt">${esc(title)}</div>
    ${pe ? `<div class="pp">親: ${esc((pe.p.data('label') || '').split('\n')[0])}${pe.via.length ? ` › ${esc(pe.via.join(' › '))}` : ''}</div>` : '<div class="pp">起点</div>'}
    ${rel ? `<div class="pr">${rel}</div>` : ''}
    ${kids.length ? `<div class="pk">子: ${Object.entries(kidCount).map(([t, c]) => `${esc(t)} ${c}`).join(' ・ ')}</div>` : ''}`;
  const box = $('#cy').getBoundingClientRect(); const pop = $('#pop');
  pop.style.display = 'block';
  const x = Math.min(rp.x + 18, box.width - pop.offsetWidth - 8), y = Math.min(rp.y + 18, box.height - pop.offsetHeight - 8);
  pop.style.left = `${Math.max(8, x)}px`; pop.style.top = `${Math.max(8, y) + $('#toolbar').offsetHeight}px`;
}

// ---- 2つのエンティティの関係（元データの行をそのまま列挙） ----
function userBoothLines(uid, bid) {
  const L = [];
  const ci = rev('users', uid, 'check_ins').filter((c) => c.booth_id === bid);
  L.push(ci.length ? `訪問: ${ci.map((c) => `${c.visit_order}番目 ${hhmm(c.checked_in_at)}${c.checkin_method ? `（${esc(c.checkin_method)}）` : ''}`).join('、')}` : '<span class="no">訪問: なし</span>');
  const rt = rev('users', uid, 'booth_ratings').filter((r) => r.booth_id === bid);
  L.push(rt.length ? rt.map((r) => `評価: ★${r.rating}/${r.scale}${r.is_hidden === '1' ? '（非表示）' : ''}`).join('、') : '<span class="no">評価: なし</span>');
  const cm = rt.filter(hasComment);
  L.push(cm.length ? cm.map((r) => `コメント: 「${esc(r.comment)}」`).join('<br>') : '<span class="no">コメント: なし</span>');
  const vs = rev('users', uid, 'award_votes').filter((v) => v.booth_id === bid);
  L.push(vs.length ? `アワード投票: ${vs.map((v) => esc(awardName(v.award_id))).join('、')}` : '<span class="no">アワード投票: なし</span>');
  const rs = assigned(rev('users', uid, 'recommendation_scores')).filter((r) => r.booth_id === bid);
  L.push(rs.length ? `推薦: ${rs.map((r) => `${hhmm(r.created_at)} ${esc(strategyOf(r))} rank${r.rank_in_event}`).join('、')}` : '<span class="no">推薦: なし</span>');
  const card = userCard(uid);
  const cells = card ? rev('bingo_cards', card.id, 'bingo_cells').filter((c) => c.booth_id === bid) : [];
  L.push(cells.length ? `ビンゴ: ${cells.map((c) => `マス${c.position} ${ja(c.source)}${c.is_achieved === '1' ? ' 達成' : ''}`).join('、')}` : '<span class="no">ビンゴ: このブースのマスなし</span>');
  if (rev('users', uid, 'exhibitor_booths').some((x) => x.booth_id === bid)) L.push('出展者として担当');
  return L;
}
function relationLines(pn, cn) {
  const [pt, pid, ct, cid] = [pn.data('table'), pn.data('pk'), cn.data('table'), cn.data('pk')];
  const pair = (a, b) => (pt === a && ct === b) || (pt === b && ct === a);
  const id = (t) => (pt === t ? pid : cid);
  if (pair('users', 'booths')) return userBoothLines(id('users'), id('booths'));
  if (pair('users', 'categories')) {
    const s = survey(id('users')) || {}; const c = id('categories');
    const n = userCheckins(id('users')).filter((x) => BY.booths.get(x.booth_id)?.category_id === c).length;
    return [`アンケートの関心: ${s.interest_categories?.includes(c) ? 'あり' : 'なし'}${s.top_interest_category === c ? '（一番の関心）' : ''}`, `このカテゴリのブースへの訪問: ${n}件`];
  }
  if (pair('users', 'awards')) {
    const vs = rev('users', id('users'), 'award_votes').filter((v) => v.award_id === id('awards'));
    return [vs.length ? `投票先: ${vs.map((v) => esc(boothName(v.booth_id))).join('、')}` : '<span class="no">このアワードへの投票: なし</span>'];
  }
  if (pair('booths', 'awards')) {
    const vs = rev('booths', id('booths'), 'award_votes').filter((v) => v.award_id === id('awards'));
    return [`このアワードでの得票: ${vs.length}票`];
  }
  if (pair('booths', 'categories')) return [BY.booths.get(id('booths'))?.category_id === id('categories') ? 'このカテゴリに所属' : '所属していない'];
  if (pair('users', 'users')) return ['ユーザー同士（共通のブースを経由）'];
  if (pt === 'events') { const E = ENTITY[ct]; const r = BY[ct]?.get(cid); return E && r ? [`イベント内の${E.label}: ${esc(E.sub(r).replace(/\n/g, ' ・ '))}`] : []; }
  if (ct === 'bingo_cells') { const c = BY.bingo_cells.get(cid); return [`マス${c.position} ・ ${esc(boothName(c.booth_id))} ・ ${ja(c.source)}${c.is_achieved === '1' ? ' ・ 達成' : ''}`]; }
  if (pt === 'bingo_cells' && ct === 'booths') return ['このマスに割り当てられたブース'];
  return [];
}

function showRelation(n) {
  const box = $('#relation'); if (!box) return;
  const pe = parentEntity(n);
  const title = (n.data('label') || '').split('\n')[0];
  if (!pe || !n.hasClass('entity')) {
    box.innerHTML = `<div class="rh">ホバー中: <b>${esc(title)}</b></div><div class="muted">${pe ? '親のエンティティとの関係はこのノードの種類にはありません' : '起点のノードです（親なし）'}</div>`;
  } else {
    const ptitle = (pe.p.data('label') || '').split('\n')[0];
    box.innerHTML = `<div class="rh"><span class="badge" style="background:${pe.p.data('color')}">${esc(ENTITY[pe.p.data('table')]?.label || '')}</span> ${esc(ptitle)}
      <span class="muted">→</span> <span class="badge" style="background:${n.data('color')}">${esc(ENTITY[n.data('table')]?.label || '')}</span> ${esc(title)}</div>
      ${pe.via.length ? `<div class="muted">経路: ${esc(pe.via.join(' › '))}</div>` : ''}
      <div class="rl">${relationLines(pe.p, n).map((l) => `<div>${l}</div>`).join('') || '<div class="muted">直接の関係データなし</div>'}</div>`;
  }
  box.style.display = 'block';
}
cy.on('boxend', () => setTimeout(updateUI, 0));
cy.on('select unselect', () => updateUI());

$('#right-close').onclick = () => $('#right').classList.add('closed');
$('#btn-panel').onclick = () => $('#right').classList.toggle('closed');
$('#btn-remove').onclick = () => { if (!cy.$('node:selected').length) return; snapshot(); removeNodes(cy.$('node:selected')); autoArrange(); };
$('#btn-clear').onclick = () => { if (!cy.elements().length) return; snapshot(); cy.elements().remove(); showDetail(null); updateUI(); };
$('#btn-undo').onclick = undo;
$('#btn-redo').onclick = redo;
$('#btn-event').onclick = () => { const ev = T.events?.[0]; if (!ev) return; snapshot(); const n = addEntity('events', ev.id, { pos: center() }); deoverlap(n); cy.$(':selected').unselect(); n.select(); showDetail(n); reveal(n); updateUI(); };
$('#btn-fit').onclick = () => cy.animate({ fit: { padding: 50 } }, { duration: 250 });
$('#btn-layout').onclick = () => arrange('parent');
$('#arrange').onchange = (e) => { if (e.target.value) arrange(e.target.value); e.target.value = ''; };
$('#btn-cluster').onclick = () => { snapshot(); cluster(); };
$('#btn-uncluster').onclick = () => { if (cy.nodes('.cluster').length) snapshot(); cy.nodes('.cluster').forEach((c) => { c.children().move({ parent: null }); c.remove(); }); updateUI(); };
function layout() { arrange('force'); }

// ---- 親子の距離を保つ整列 ----
// owner（そのノードを広げた親）で木を作り、各ノードに「子孫の葉の数」に比例した角度の扇を割り当てる。
// 子は親の扇の中にしか置かないので、親が違う孫同士は別の扇に分かれて近づかない。
// 半径は「その段のノードが扇の中に収まる長さ」から段ごとに決める。起点ごとの円は棚詰めで並べる。
function treeLayout({ animate = true, fit = false } = {}) {
  const nodes = cy.nodes().filter((n) => !n.isParent());
  if (!nodes.length) return;
  const ids = new Set(nodes.map((n) => n.id()));
  const kids = new Map(); const roots = [];
  nodes.forEach((n) => {
    const o = n.data('owner');
    if (o && ids.has(o)) { if (!kids.has(o)) kids.set(o, []); kids.get(o).push(n); } else roots.push(n);
  });
  const W = new Map();
  // 子を持つノードには「余白」ぶんの重みを足す。扇の両端に余白を残すので、親が違う子同士の間が必ず空く
  const PAD = 1.6;
  const weight = (n) => { const ks = kids.get(n.id()) || []; const w = ks.length ? ks.reduce((a, k) => a + weight(k), 0) + PAD : 1; W.set(n.id(), w); return w; };
  const sizeOf = new Map();
  const size = (n) => { if (!sizeOf.has(n.id())) { const b = n.boundingBox({ includeLabels: true }); sizeOf.set(n.id(), { w: b.w, h: b.h }); } return sizeOf.get(n.id()); };
  const GAP = 16;

  const trees = roots.map((root) => {
    weight(root);
    const rel = new Map(); const levels = [];
    const walk = (n, a0, a1, d, i = 0, nsib = 1) => {
      rel.set(n.id(), { a0, a1, d, i, nsib }); (levels[d] ??= []).push(n);
      const ks = kids.get(n.id()) || []; if (!ks.length) return;
      const tot = W.get(n.id()); const inner = tot - PAD;
      const m = d === 0 ? 0 : ((a1 - a0) * PAD) / tot / 2;    // 起点は全周を使うので余白なし
      const use = d === 0 ? a1 - a0 : a1 - a0 - 2 * m;
      const base = d === 0 ? inner + PAD : inner;
      let a = a0 + m;
      ks.forEach((k, i) => { const span = (use * W.get(k.id())) / base; walk(k, a, a + span, d + 1, i, ks.length); a += span; });
    };
    walk(root, -Math.PI / 2, (3 * Math.PI) / 2, 0);
    // 兄弟が多い段は内側/外側の2列に互い違いに置く（弧に必要な長さが半分で済むので、親から遠くならない）
    const r = [0], ringH = [0], stagger = [false];
    for (let d = 1; d < levels.length; d++) {
      const prevH = Math.max(...levels[d - 1].map((n) => size(n).h)), curH = Math.max(...levels[d].map((n) => size(n).h));
      let need = r[d - 1] + ringH[d - 1] + (prevH + curH) / 2 + (d === 1 ? 50 : 40);
      const base = need;
      let arcNeed = 0;
      for (const n of levels[d]) {
        const { a0, a1 } = rel.get(n.id()); const span = Math.max(a1 - a0, 1e-3);
        arcNeed = Math.max(arcNeed, ((size(n).w + size(n).h) / 2 + GAP) / Math.min(span, Math.PI));
      }
      stagger[d] = arcNeed > base + 60;
      if (stagger[d]) arcNeed *= 0.55;
      need = Math.max(need, arcNeed);
      r[d] = Math.min(need, r[d - 1] + 560);
      ringH[d] = stagger[d] ? curH + 22 : 0;
    }
    const local = {};
    rel.forEach((v, id) => {
      if (v.d === 0) { local[id] = { x: 0, y: 0 }; return; }
      const a = (v.a0 + v.a1) / 2; const rr = r[v.d] + (stagger[v.d] && v.nsib > 2 && v.i % 2 ? ringH[v.d] : 0);
      local[id] = { x: rr * Math.cos(a), y: rr * Math.sin(a) };
    });
    const ext = Object.entries(local).reduce((m, [id, p]) => {
      const sz = size(cy.getElementById(id));
      return { x1: Math.min(m.x1, p.x - sz.w / 2), x2: Math.max(m.x2, p.x + sz.w / 2), y1: Math.min(m.y1, p.y - sz.h / 2), y2: Math.max(m.y2, p.y + sz.h / 2) };
    }, { x1: 0, x2: 0, y1: 0, y2: 0 });
    return { root, local, ext, group: root.parent().nonempty() ? root.parent().id() : '' };
  });

  // 起点ごとの塊を、グループ（類似クラスタ）ごとに棚詰め
  const pos = {};
  const groups = {};
  trees.forEach((t) => (groups[t.group] ??= []).push(t));
  const start = cy.nodes().boundingBox();
  let gx = start.x1;
  for (const ts of Object.values(groups)) {
    ts.sort((a, b) => (b.ext.y2 - b.ext.y1) - (a.ext.y2 - a.ext.y1));
    const area = ts.reduce((a, t) => a + (t.ext.x2 - t.ext.x1 + 120) * (t.ext.y2 - t.ext.y1 + 120), 0);
    const rowMax = Math.max(Math.sqrt(area) * 1.3, ...ts.map((t) => t.ext.x2 - t.ext.x1 + 120));
    let x = 0, y = 0, rowH = 0, gw = 0;
    for (const t of ts) {
      const w = t.ext.x2 - t.ext.x1 + 120, h = t.ext.y2 - t.ext.y1 + 120;
      if (x > 0 && x + w > rowMax) { x = 0; y += rowH; rowH = 0; }
      const ox = gx + x - t.ext.x1 + 60, oy = start.y1 + y - t.ext.y1 + 60;
      for (const [id, p] of Object.entries(t.local)) pos[id] = { x: p.x + ox, y: p.y + oy };
      x += w; rowH = Math.max(rowH, h); gw = Math.max(gw, x);
    }
    gx += gw + 200;
  }
  const done = () => { deoverlap(cy.nodes(), 60); if (fit) cy.animate({ fit: { padding: 50 } }, { duration: 250 }); };
  if (!animate) { nodes.forEach((n) => { if (pos[n.id()]) n.position(pos[n.id()]); }); done(); return; }
  const l = nodes.layout({ name: 'preset', positions: (n) => pos[n.id()] || n.position(), animate: true, animationDuration: 380, fit: false });
  l.one('layoutstop', done); l.run();
}
const autoArrange = () => { if ($('#opt-auto').checked) treeLayout({ animate: true }); };

// ---- 整理 ----
function runLayout(opts, after) {
  const l = cy.layout({ animate: true, animationDuration: 450, padding: 50, fit: true, ...opts });
  l.one('layoutstop', () => { deoverlap(cy.nodes(), 80); if (after) after(); cy.animate({ fit: { padding: 50 } }, { duration: 250 }); updateUI(); });
  l.run();
}
const depth = (n) => { let d = 0, c = n; while (c && c.data('owner') && d < 50) { c = cy.getElementById(c.data('owner')); d++; } return d; };
function arrange(kind) {
  if (!cy.nodes().length) return;
  snapshot();
  const sel = cy.$('node:selected');
  switch (kind) {
    case 'parent':     // 親子の距離を保つ（おすすめ）
      treeLayout({ animate: true, fit: true }); return;
    case 'force':      // 力学モデル（つながっているものが近く）
      return runLayout({ name: 'cose', nodeRepulsion: () => 16000, idealEdgeLength: () => 110, nodeOverlap: 20, componentSpacing: 120, nestingFactor: 0.8, randomize: false });
    case 'tree': {     // 起点から外へ、階層ごとに
      const roots = cy.nodes('.entity.root');
      return runLayout({ name: 'breadthfirst', roots: roots.length ? roots : undefined, directed: false, spacingFactor: 1.25, avoidOverlap: true, grid: false });
    }
    case 'radial':     // 同心円（中心ほど起点に近い）
      return runLayout({ name: 'concentric', concentric: (n) => 10 - depth(n), levelWidth: () => 1, minNodeSpacing: 30, avoidOverlap: true });
    case 'grid':
      return runLayout({ name: 'grid', avoidOverlap: true, avoidOverlapPadding: 20, condense: false });
    case 'bytype': {   // 種類ごとに列をつくる
      const order = ['events', 'users', 'booths', 'awards', 'categories', 'bingo_cells'];
      const col = (n) => (n.hasClass('entity') ? order.indexOf(n.data('table')) : n.hasClass('facet') || n.hasClass('bucket') ? order.length : order.length + 1);
      const cols = {};
      cy.nodes().filter((n) => !n.isParent()).forEach((n) => { (cols[col(n)] ??= []).push(n); });
      Object.entries(cols).forEach(([c, ns]) => ns.forEach((n, i) => n.position({ x: Number(c) * 260, y: i * 90 })));
      deoverlap(cy.nodes(), 40); cy.animate({ fit: { padding: 50 } }, { duration: 250 }); return;
    }
    case 'spread':     // 重なりだけ解消（位置関係はそのまま）
      deoverlap(cy.nodes(), 120); return;
    case 'collapse':   // メニューを全部閉じて起点だけに
      cy.nodes('.entity.root.open').forEach((n) => { collapse(n); n.removeClass('open'); });
      cy.nodes('.entity.open').removeClass('open'); updateUI(); return;
    case 'keep': {     // 選択したもの（とその展開）だけ残す
      if (!sel.length) { alert('残したいノードを選択してから使ってください'); undoStack.pop(); historyButtons(); return; }
      const keep = new Set();
      sel.forEach((n) => { let c = n; while (c && c.nonempty()) { keep.add(c.id()); c = c.data('owner') ? cy.getElementById(c.data('owner')) : null; } subtree(n).forEach((id) => keep.add(id)); });
      cy.nodes().filter((n) => !keep.has(n.id()) && !n.isParent()).remove(); dropEmptyClusters(); updateUI(); return;
    }
    case 'info':       // 値・メモのノードを消して見通しをよくする
      cy.nodes('.info, .more').remove(); updateUI(); return;
  }
}
document.addEventListener('keydown', (e) => {
  if (/INPUT|SELECT/.test(e.target.tagName)) { if (e.key === 'Escape') e.target.blur(); return; }
  if (e.code === 'Space' && !spaceDown) { spaceDown = true; cy.userPanningEnabled(true); cy.boxSelectionEnabled(false); e.preventDefault(); }
  else if (e.key === '/') { e.preventDefault(); $('#q').focus(); }
  else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); }
  else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'y') { e.preventDefault(); redo(); }
  else if (e.key === 'Delete' || e.key === 'Backspace') $('#btn-remove').click();
  else if (e.key === 'l') arrange('parent');
  else if (e.key === 'o') arrange('spread');
  else if (e.key === 'f') $('#btn-fit').click();
  else if (e.key === 'Escape') { cy.$(':selected').unselect(); showDetail(null); }
});

// ============================================================
// 7. 左パネル: 対象・絞り込み・並び順・個数
// ============================================================
let type = 'users';
let filterState = {};
let shuffleSeed = Date.now();
let pickMode = 'random';
function renderMode() {
  document.querySelectorAll('#mode button').forEach((b) => b.classList.toggle('active', b.dataset.mode === pickMode));
  $('#sort').disabled = pickMode === 'random'; $('#reroll').disabled = pickMode !== 'random';
}
$('#mode').addEventListener('click', (e) => { const b = e.target.closest('button'); if (!b) return; pickMode = b.dataset.mode; renderMode(); refreshList(); });
const defaultFilters = (t) => (t === 'users' ? { role: ['participant'] } : {});

function renderSorts() {
  const E = ENTITY[type]; const cur = $('#sort').value;
  const ms = E.metrics || { visits: [type === 'awards' ? '票数' : '訪問数', E.visits] };
  $('#sort').innerHTML = Object.entries(ms).map(([k, [l]]) =>
    `<option value="${k}:desc">${l}が多い順</option><option value="${k}:asc">${l}が少ない順</option>`).join('') + '<option value="name">名前・ID順</option>';
  if ([...$('#sort').options].some((o) => o.value === cur)) $('#sort').value = cur;
}
function renderTypes() {
  $('#types').innerHTML = Object.entries(ENTITY).map(([t, E]) =>
    `<button data-t="${t}" class="${t === type ? 'active' : ''}">${E.icon} ${E.label} <span class="muted">${T[t]?.length ?? 0}</span></button>`).join('');
}
$('#types').addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return;
  type = b.dataset.t; filterState = defaultFilters(type); renderTypes(); renderSorts(); renderFilters(); refreshList();
});

function allFilters() {
  const E = ENTITY[type]; const fs = E.filters();
  if (E.flags) fs.push({ key: 'flag', label: '条件', values: Object.keys(E.flags), show: (k) => E.flags[k][0], get: (r) => Object.keys(E.flags).filter((k) => E.flags[k][1](r)) });
  return fs;
}
function renderFilters() {
  const fs = allFilters();
  $('#filters').innerHTML = fs.map((f) => {
    const on = filterState[f.key] || [];
    return `<div class="fgroup"><div class="fhead"><b>${f.label}</b><span class="muted">${f.key === 'flag' ? 'すべて満たす' : 'どれか'}${on.length ? ` ・ ${on.length}件選択` : ''}</span>
      ${on.length ? `<a class="fclear" data-k="${f.key}">解除</a>` : ''}</div>
      <div class="fchips">${f.values.filter((v) => v != null).map((v) =>
        `<button class="fchip ${on.includes(v) ? 'on' : ''}" data-k="${f.key}" data-v="${esc(v)}">${esc(f.show ? f.show(v) : ja(v))}</button>`).join('')}</div></div>`;
  }).join('');
}
$('#filters').addEventListener('click', (e) => {
  const c = e.target.closest('.fchip'); const x = e.target.closest('.fclear');
  if (x) { delete filterState[x.dataset.k]; }
  else if (c) {
    const cur = new Set(filterState[c.dataset.k] || []);
    cur.has(c.dataset.v) ? cur.delete(c.dataset.v) : cur.add(c.dataset.v);
    if (cur.size) filterState[c.dataset.k] = [...cur]; else delete filterState[c.dataset.k];
  } else return;
  renderFilters(); refreshList();
});

function rng(seed) { let s = seed % 2147483647; return () => (s = (s * 48271) % 2147483647) / 2147483647; }

function candidates() {
  const E = ENTITY[type]; const q = $('#q').value.trim().toLowerCase();
  const fs = allFilters();
  const minv = $('#minv').value === '' ? -Infinity : Number($('#minv').value);
  const maxv = $('#maxv').value === '' ? Infinity : Number($('#maxv').value);
  let rows = (T[type] || []).filter((r) => {
    if (q && !E.search(r).toLowerCase().includes(q)) return false;
    for (const f of fs) {
      const sel = filterState[f.key]; if (!sel?.length) continue;
      const got = f.get(r);
      if (f.key === 'flag' ? !sel.every((v) => got.includes(v)) : !sel.some((v) => got.includes(v))) return false;
    }
    const n = E.visits(r); return n >= minv && n <= maxv;
  });
  const sort = pickMode === 'random' ? 'random' : $('#sort').value;
  if (sort === 'random') { const rand = rng(shuffleSeed); rows = rows.map((r) => [rand(), r]).sort((a, b) => a[0] - b[0]).map((x) => x[1]); }
  else if (sort !== 'name') {
    const [m, dir] = sort.split(':'); const f = E.metrics?.[m]?.[1] || E.visits;
    const val = new Map(rows.map((r) => [r, f(r)]));
    rows.sort((a, b) => (dir === 'asc' ? val.get(a) - val.get(b) : val.get(b) - val.get(a)));
  } else rows.sort((a, b) => E.title(a).localeCompare(E.title(b), 'ja'));
  return rows;
}

function refreshList() {
  const rows = candidates(); const E = ENTITY[type]; const n = Math.max(1, Number($('#n').value) || 1);
  const how = pickMode === 'random' ? 'ランダムに' : `「${$('#sort').selectedOptions[0]?.text ?? ''}」で上から`;
  $('#hits').innerHTML = `条件に合う${E.label}: <b>${rows.length}</b>${E.unit} → ${how} <b>${Math.min(n, rows.length)}</b>${E.unit}を表示`;
  $('#results').innerHTML = rows.slice(0, 300).map((r, i) => {
    const id = eid(type, r.id);
    return `<li data-id="${esc(r.id)}" class="${i < n ? 'pick' : ''} ${cy.getElementById(id).nonempty() ? 'on' : ''}">
      <div class="t">${esc(E.title(r))}</div><div class="s">${esc(E.sub(r).replace(/\n/g, ' ・ '))}</div></li>`;
  }).join('');
  scheduleSummary();
}
let timer;
for (const el of ['#q', '#minv', '#maxv', '#n']) $(el).addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(refreshList, 120); });
$('#sort').addEventListener('change', refreshList);
$('#reroll').onclick = () => { shuffleSeed = Date.now(); refreshList(); };
$('#add').onclick = () => addRoots(false);
$('#replace').onclick = () => addRoots(true);
$('#results').addEventListener('click', (e) => {
  const li = e.target.closest('li'); if (!li) return;
  snapshot();
  const n = addEntity(type, li.dataset.id, { pos: center() }); deoverlap(n);
  cy.$(':selected').unselect(); n.select(); cy.animate({ center: { eles: n } }, { duration: 250 });
  showDetail(n); updateUI();
});

function addRoots(replace) {
  snapshot();
  if (replace) cy.elements().remove();
  const rows = candidates().slice(0, Math.max(1, Number($('#n').value) || 1));
  const fresh = rows.filter((r) => cy.getElementById(eid(type, r.id)).empty());
  // 既存ノードの右側に格子状に並べる
  const bb = cy.nodes().nonempty() ? cy.nodes().boundingBox() : { x2: 0, y1: 0 };
  const cols = Math.ceil(Math.sqrt(fresh.length));
  const added = fresh.map((r, i) => addEntity(type, r.id, { pos: { x: bb.x2 + 200 + (i % cols) * 260, y: bb.y1 + Math.floor(i / cols) * 220 } }));
  if ($('#opt-auto').checked) treeLayout({ animate: false }); else deoverlap(cy.collection(added.filter(Boolean)));
  cy.animate({ fit: { padding: 60 } }, { duration: 300 });
  updateUI();
}

// ============================================================
// 8. 似ているものでまとめる（属性ベクトル → k-means → 2次元に配置）
// ============================================================
function shares(values) { const m = {}; values.forEach((v) => { if (v != null) m[v] = (m[v] || 0) + 1 / values.length; }); return m; }

const VECTOR = {
  users: (id) => {
    const s = survey(id) || {}; const v = {};
    for (const k of ['gender', 'age_range', 'affiliation', 'exploration_disposition']) if (s[k]) v[`${{ gender: '性別', age_range: '年代', affiliation: '所属', exploration_disposition: '探索' }[k]}:${ja(s[k])}`] = 1;
    [].concat(s.occupation || []).forEach((o) => { v[`職業:${ja(o)}`] = 1; });
    (s.interest_categories || []).forEach((c) => { v[`関心:${catName(c)}`] = 1; });
    const cats = userCheckins(id).map((ci) => BY.booths.get(ci.booth_id)?.category_id);
    for (const [c, w] of Object.entries(shares(cats))) v[`訪問:${catName(c)}`] = w;
    return v;
  },
  booths: (id) => {
    const b = BY.booths.get(id); const v = { [`カテゴリ:${catName(b.category_id)}`]: 1 };
    const visitors = boothCheckins(id).map((ci) => survey(ci.user_id) || {});
    for (const k of ['age_range', 'affiliation', 'gender']) for (const [x, w] of Object.entries(shares(visitors.map((s) => s[k])))) v[`来場者${{ age_range: '年代', affiliation: '所属', gender: '性別' }[k]}:${ja(x)}`] = w;
    for (const [x, w] of Object.entries(shares(visitors.map((s) => [].concat(s.occupation || [])[0])))) v[`来場者職業:${ja(x)}`] = w;
    const rt = rev('booths', id, 'booth_ratings').map((r) => num(r.rating));
    if (rt.length) v['評価の高さ'] = (rt.reduce((a, b2) => a + b2, 0) / rt.length - 1) / 3;
    const maxV = Math.max(...T.booths.map((x) => rev('booths', x.id, 'check_ins').length), 1);
    v['訪問の多さ'] = Math.log1p(rev('booths', id, 'check_ins').length) / Math.log1p(maxV);
    return v;
  },
};
const PALETTE = ['#2563eb', '#16a34a', '#dc2626', '#9333ea', '#ea580c', '#0891b2', '#ca8a04', '#db2777'];

function cluster() {
  const targets = ['users', 'booths'].map((t) => ({ t, nodes: cy.nodes(`.entity[table="${t}"]`) })).filter((x) => x.nodes.length >= 3);
  if (!targets.length) { alert('ユーザーかブースをキャンバスに3つ以上置いてから使ってください'); return; }
  $('#btn-uncluster').click();
  let offsetY = 0;
  for (const { t, nodes } of targets) {
    const ids = nodes.map((n) => n.data('pk'));
    const maps = ids.map((id) => VECTOR[t](id));
    const keys = [...new Set(maps.flatMap(Object.keys))];
    const X = maps.map((m) => keys.map((k) => m[k] || 0));
    const k = Number($('#k').value) || Math.min(6, Math.max(2, Math.round(Math.sqrt(ids.length / 2))));
    const { assign, centroids } = kmeans(X, Math.min(k, ids.length));
    const mean = keys.map((_, j) => X.reduce((a, x) => a + x[j], 0) / X.length);
    const xy = pca2(centroids);
    centroids.forEach((c, ci) => {
      const members = nodes.filter((_, i) => assign[i] === ci); if (!members.length) return;
      const top = keys.map((key, j) => ({ key, d: c[j] - mean[j], v: c[j] })).filter((x) => x.v >= 0.3 && x.d > 0.05).sort((a, b) => b.d - a.d).slice(0, 3);
      const color = PALETTE[ci % PALETTE.length];
      const box = cy.add({ group: 'nodes', classes: 'cluster', data: { id: `cluster#${++seq}`, kind: 'cluster', color,
        label: `グループ${ci + 1}（${members.length}${ENTITY[t].unit}）  ${top.map((x) => x.key).join(' / ') || '特徴なし'}`, top, ctype: t } });
      const cx = xy[ci][0] * 520, cyy = xy[ci][1] * 380 + offsetY;
      const cols = Math.ceil(Math.sqrt(members.length));
      members.forEach((m, i) => {
        const to = { x: cx + (i % cols) * 190, y: cyy + Math.floor(i / cols) * 150 };
        const dx = to.x - m.position('x'), dy = to.y - m.position('y');
        m.move({ parent: box.id() });
        m.position(to);
        subtree(m).forEach((id) => { const d = cy.getElementById(id); if (!d.isParent()) d.shift({ x: dx, y: dy }); });
      });
    });
    offsetY += 1400;
  }
  cy.animate({ fit: { padding: 60 } }, { duration: 400 });
  updateUI();
}
function dropEmptyClusters() { cy.nodes('.cluster').filter((c) => c.children().empty()).remove(); }

function kmeans(X, k) {
  const rand = rng(20261016); const n = X.length;
  const d2 = (a, b) => a.reduce((s, v, i) => s + (v - b[i]) ** 2, 0);
  const C = [X[Math.floor(rand() * n)].slice()];
  while (C.length < k) {   // k-means++
    const w = X.map((x) => Math.min(...C.map((c) => d2(x, c))));
    let r = rand() * w.reduce((a, b) => a + b, 0), i = 0;
    while (i < n - 1 && (r -= w[i]) > 0) i++;
    C.push(X[i].slice());
  }
  let assign = new Array(n).fill(0);
  for (let it = 0; it < 50; it++) {
    const next = X.map((x) => C.reduce((best, c, ci) => (d2(x, c) < d2(x, C[best]) ? ci : best), 0));
    const same = next.every((v, i) => v === assign[i]); assign = next;
    C.forEach((c, ci) => { const m = X.filter((_, i) => assign[i] === ci); if (m.length) C[ci] = c.map((_, j) => m.reduce((a, x) => a + x[j], 0) / m.length); });
    if (same && it) break;
  }
  return { assign, centroids: C };
}
function pca2(M) {   // 重心を2次元に落として、似たグループ同士を近くに置く
  if (M.length < 3) return M.map((_, i) => [i * 1.2, 0]);
  const d = M[0].length; const mu = Array.from({ length: d }, (_, j) => M.reduce((a, r) => a + r[j], 0) / M.length);
  const Y = M.map((r) => r.map((v, j) => v - mu[j]));
  const comps = [];
  for (let c = 0; c < 2; c++) {
    let v = Array.from({ length: d }, (_, j) => Math.sin(j + 1 + c));
    for (let it = 0; it < 100; it++) {
      let w = new Array(d).fill(0);
      for (const y of Y) { const p = y.reduce((a, x, j) => a + x * v[j], 0); y.forEach((x, j) => { w[j] += p * x; }); }
      for (const u of comps) { const p = w.reduce((a, x, j) => a + x * u[j], 0); w = w.map((x, j) => x - p * u[j]); }
      const l = Math.hypot(...w) || 1; v = w.map((x) => x / l);
    }
    comps.push(v);
  }
  const P = Y.map((y) => comps.map((v) => y.reduce((a, x, j) => a + x * v[j], 0)));
  const s = Math.max(...P.flat().map(Math.abs), 1e-9);
  return P.map(([a, b]) => [a / s, b / s]);
}

// ============================================================
// 9. 右パネル: いま描いているものを文章で
// ============================================================
function rawTable(t, r) {
  return `<div class="muted" style="margin-top:10px">${esc(t)}（元データ）</div><table class="kv">${Object.entries(r).map(([k, v]) => {
    let cell = v == null ? '<span class="null">null</span>' : esc(v);
    const target = FK[t]?.[k];
    if (v != null && target && BY[target]?.has(v)) cell = `<a class="ref" data-t="${target}" data-pk="${esc(v)}">${esc(v)}</a><div class="muted">→ ${esc(ENTITY[target] ? ENTITY[target].title(BY[target].get(v)) : target)}</div>`;
    else if (typeof v === 'string' && /^[[{]/.test(v)) { try { cell = `<pre class="json">${esc(JSON.stringify(JSON.parse(v), null, 2))}</pre>`; } catch { /* そのまま */ } }
    return `<tr><th>${esc(k)}${/_at$/.test(k) && v ? '<span class="tz" title="DB の保存値そのまま。列によって JST/UTC が混在">保存値</span>' : ''}</th><td>${cell}</td></tr>`;
  }).join('')}</table>`;
}
function itemText(it) {
  if (it.entity) { const r = BY[it.entity[0]]?.get(it.entity[1]); return `${r ? ENTITY[it.entity[0]].title(r) : short(it.entity[1])}${it.edge ? `　<span class="sub">${esc(it.edge)}</span>` : ''}`; }
  if (it.bucket) return `${esc(it.bucket)}`;
  if (it.cell) { const b = BY.booths?.get(it.cell.booth_id); return `マス${it.cell.position}${it.cell.is_achieved === '1' ? ' ✓' : ''}　${esc(b ? `#${b.display_code} ${b.name}` : '（未公開）')}　<span class="sub">${esc(ja(it.cell.source))}</span>`; }
  return esc(it.info).replace(/\n/g, ' ');
}

function showHelp() {
  $('#detail').innerHTML = `<h2>使い方</h2>
    <ol style="padding-left:18px;margin:6px 0">
      <li>左で「何を見る？」「絞り込み」「個数」を決めて <b>キャンバスに追加</b></li>
      <li>ノードをクリック → <b>メニュー</b>（訪問したブース／ビンゴカード…）が開く</li>
      <li>メニューをクリック → 中身が広がる。もう一度クリックで閉じる</li>
      <li>線の文字は「何番目に・何時に・★いくつ」など。線をクリックで元データ</li>
    </ol>
    <h3>形の意味</h3>
    ${Object.values(ENTITY).map((E) => `<div><span class="shape" style="color:${E.color}">${E.icon}</span> ${E.label}</div>`).join('')}
    <div><span class="shape">▭</span> メニュー（白抜き＝閉、塗り＝開）</div>
    <div><span class="shape">⌂</span> まとまり（★4 25人 など。クリックで中身）</div>
    <div><span class="shape" style="color:#06b6d4">▦</span> ビンゴのマス（塗り＝達成）</div>
    <div><span class="shape">▫</span> 値・メモ（黄色＝データ未取得）</div>
    <h3>操作</h3>
    <div class="muted">ドラッグ: 範囲選択 ・ 2本指スワイプ: 移動 ・ ピンチ: 拡大縮小 ・ Space+ドラッグ: 移動<br>
    Delete: 選択と、そこから広げたものを削除 ・ L: 整列 ・ F: 全体表示 ・ Esc: 選択解除</div>`;
}

function showDetail(node) {
  const el = $('#detail');
  if (node) { $('#right').classList.remove('closed'); showTab('detail'); }
  if (!node) { showTab('summary'); return showHelp(); }
  const d = node.data();
  if (d.kind === 'entity') {
    const r = BY[d.table].get(d.pk); const E = ENTITY[d.table];
    el.innerHTML = `<span class="badge" style="background:${d.color}">${E?.label ?? d.table}</span>
      <h2>${esc(E ? E.title(r) : d.label)}</h2><div class="muted">${esc(E ? E.sub(r) : '').replace(/\n/g, ' ・ ')}</div>
      ${FACETS[d.table] ? `<h3>メニュー（クリックでキャンバスに展開）</h3><div class="list">${FACETS[d.table].map((f) => `<div class="item" data-facet="${f.key}">▸ ${f.title} <b>${f.items(r).length}</b></div>`).join('')}</div>` : ''}
      ${rawTable(d.table, r)}
      ${d.table === 'users' && survey(d.pk) ? rawTable('user_survey_answers', survey(d.pk).row) : ''}
      ${d.table === 'events' ? `<h3>参加者（role=participant）について</h3>${SUMMARY.users(participants())}<h3>ブースについて</h3>${SUMMARY.booths(T.booths || [])}` : ''}`;
    el.querySelectorAll('[data-facet]').forEach((x) => { x.onclick = () => {
      snapshot();
      if (!node.hasClass('open')) toggleMenu(node);
      const fn = cy.nodes(`.facet[owner="${node.id()}"][facet="${x.dataset.facet}"]`)[0];
      if (fn && !fn.hasClass('open') && fn.data('items').length) toggleFacet(fn);
      autoArrange(); updateUI();
    }; });
  } else if (d.kind === 'facet' || d.kind === 'bucket') {
    const owner = cy.getElementById(d.owner);
    el.innerHTML = `<span class="badge" style="background:${d.color}">${d.kind === 'facet' ? 'メニュー' : 'まとまり'}</span>
      <h2>${esc(d.title)}</h2><div class="muted">${esc((owner.data('label') || '').split('\n')[0])} の ${esc(d.title)} ・ ${d.items.length}件</div>
      ${d.items.length ? `<div class="list" style="margin-top:8px">${d.items.map((it, i) => `<div class="item" data-i="${i}">${i + 1}. ${itemText(it)}</div>`).join('')}</div>` : '<p class="muted">データなし</p>'}`;
    el.querySelectorAll('[data-i]').forEach((x) => { x.onclick = () => {
      const it = d.items[x.dataset.i]; snapshot();
      if (it.entity) { const n = addEntity(it.entity[0], it.entity[1], { owner: node.id(), pos: { x: node.position('x') + 160, y: node.position('y') + 40 } }); if (n) { addEdge(node, n, { owner: node.id(), label: it.edge || '', raw: it.raw }); cy.$(':selected').unselect(); n.select(); reveal(n); } }
      updateUI();
    }; });
  } else if (d.kind === 'info') {
    el.innerHTML = `<span class="badge" style="background:#64748b">値</span><h2>${esc(d.label).replace(/\n/g, '<br>')}</h2>
      ${d.note ? `<div class="hint">${esc(d.note)}</div>` : ''}${(d.raw || []).map(([t, r]) => rawTable(t, r)).join('')}`;
  } else if (d.kind === 'cluster') {
    el.innerHTML = `<span class="badge" style="background:${d.color}">似ているグループ</span><h2>${esc(d.label.split('  ')[0])}</h2>
      <h3>平均より目立つ特徴</h3>${d.top.length ? d.top.map((x) => `<div class="rel"><span>${esc(x.key)}</span><b>${Math.round(x.v * 100)}%</b></div>`).join('') : '<p class="muted">目立つ特徴なし</p>'}
      <h3>メンバー</h3><div class="list">${node.children().map((c) => `<div class="item">${esc(c.data('label').replace(/\n/g, ' ・ '))}</div>`).join('')}</div>
      <p class="muted">属性（アンケート・訪問カテゴリ、ブースは来場者構成・評価・訪問数）を並べたベクトルを k-means で分けています。グループ同士の距離も近いものほど近くに置いています。</p>`;
  } else el.innerHTML = '';
}

function showEdge(edge) {
  showTab('detail'); $('#right').classList.remove('closed');
  const d = edge.data();
  const s = edge.source().data('label')?.split('\n')[0], t = edge.target().data('label')?.split('\n')[0];
  $('#detail').innerHTML = `<span class="badge" style="background:#475569">つながり</span>
    <h2>${esc(s)} → ${esc(t)}</h2><div class="muted">${esc(d.label)}</div>
    ${(d.raw || []).map(([tb, r]) => rawTable(tb, r)).join('') || '<p class="muted">元データの行なし（メニューの線）</p>'}`;
}

$('#detail').addEventListener('click', (e) => {
  const a = e.target.closest('a.ref'); if (!a) return;
  const src = cy.$('node:selected')[0];
  const p = src ? src.position() : center();
  const n = ENTITY[a.dataset.t] ? addEntity(a.dataset.t, a.dataset.pk, { pos: { x: p.x + 160, y: p.y } }) : null;
  if (n) { deoverlap(n); cy.$(':selected').unselect(); n.select(); showDetail(n); reveal(n); updateUI(); }
});

// ============================================================
// 10. 凡例・件数
// ============================================================
function updateUI() {
  scheduleSummary();
  const n = cy.nodes().not('.cluster').length;
  $('#empty').style.display = n ? 'none' : 'flex';
  const sel = cy.$('node:selected').length;
  $('#count').textContent = n ? `ノード ${n}${sel ? ` ・ 選択 ${sel}` : ''}` : '';
  $('#btn-remove').disabled = !sel;
  const present = Object.entries(ENTITY).map(([t, E]) => [t, E, cy.nodes(`.entity[table="${t}"]`).length]).filter((x) => x[2]);
  $('#legend').style.display = present.length ? 'flex' : 'none';
  $('#legend').innerHTML = present.map(([t, E, c]) =>
    `<label><input type="checkbox" data-t="${t}" ${hiddenTypes.has(t) ? '' : 'checked'}><span style="color:${E.color}">${E.icon}</span>${E.label} <span class="muted">${c}</span></label>`).join('');
  for (const li of document.querySelectorAll('#results li')) li.classList.toggle('on', cy.getElementById(eid(type, li.dataset.id)).nonempty());
}
const hiddenTypes = new Set();
$('#legend').addEventListener('change', (e) => {
  const t = e.target.dataset.t; e.target.checked ? hiddenTypes.delete(t) : hiddenTypes.add(t);
  cy.nodes(`.entity[table="${t}"]`).toggleClass('hidden', !e.target.checked);
});

// ============================================================
// 11. 数字で見る（条件に合う行を数えるだけ。値は変えない）
// ============================================================
let summaryScope = 'filtered';
let summaryTimer;
function scheduleSummary() { clearTimeout(summaryTimer); summaryTimer = setTimeout(renderSummary, 150); }

function showTab(tab) {
  document.querySelectorAll('#tabs [data-tab]').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  $('#summary').style.display = tab === 'summary' ? 'block' : 'none';
  $('#detail').style.display = tab === 'detail' ? 'block' : 'none';
  if (tab === 'summary') renderSummary();
}
document.querySelectorAll('#tabs [data-tab]').forEach((b) => { b.onclick = () => showTab(b.dataset.tab); });

function renderEvent() {
  const ev = T.events?.[0]; if (!ev) return;
  $('#meta').innerHTML = `<b>${esc(ev.name)}</b> ${esc((ev.date_start || '').slice(0, 10))}<br>${$('#meta').innerHTML}`;
}

const pct = (a, b) => (b ? `${Math.round((a / b) * 1000) / 10}%` : '—');
const fmtN = (v) => (Number.isInteger(v) ? v.toLocaleString() : (Math.round(v * 100) / 100).toLocaleString());
function stats(arr) {
  if (!arr.length) return { avg: 0, med: 0, max: 0, min: 0 };
  const a = [...arr].sort((x, y) => x - y);
  return { avg: a.reduce((x, y) => x + y, 0) / a.length, med: a.length % 2 ? a[(a.length - 1) / 2] : (a[a.length / 2 - 1] + a[a.length / 2]) / 2, max: a[a.length - 1], min: a[0] };
}
function count(values) { const m = new Map(); for (const v of values) m.set(v ?? '（なし）', (m.get(v ?? '（なし）') || 0) + 1); return m; }
const tile = (label, value, sub = '') => `<div class="tile"><div class="tl">${label}</div><div class="tv">${value}</div>${sub ? `<div class="ts">${sub}</div>` : ''}</div>`;
function bars(title, m, { show = ja, filterKey, sort = true, max = 12, total } = {}) {
  let rows = [...m.entries()];
  if (sort) rows.sort((a, b) => b[1] - a[1]);
  const hidden = rows.length - max; rows = rows.slice(0, max);
  const top = Math.max(...rows.map((r) => (Array.isArray(r[1]) ? r[1][1] : r[1])), 1);
  return `<h3>${title}</h3><div class="bars">${rows.map(([k, v]) => {
    const [a, b] = Array.isArray(v) ? v : [v, null];
    const w = Array.isArray(v) ? (b ? (a / b) * 100 : 0) : (a / top) * 100;
    return `<div class="bar ${filterKey ? 'click' : ''}" ${filterKey ? `data-fk="${filterKey}" data-fv="${esc(k)}" title="クリックでこの条件に絞り込み"` : ''}>
      <span class="bl">${esc(show(k))}</span><span class="bt"><span style="width:${w}%"></span></span>
      <span class="bv">${Array.isArray(v) ? `${fmtN(a)}/${fmtN(b)} <span class="muted">${pct(a, b)}</span>` : `${fmtN(a)}${total ? ` <span class="muted">${pct(a, total)}</span>` : ''}`}</span></div>`;
  }).join('')}${hidden > 0 ? `<div class="muted">ほか ${hidden} 件</div>` : ''}</div>`;
}

function scopeRows() {
  if (summaryScope === 'filtered') return candidates();
  const sel = summaryScope === 'selected' ? cy.$(`node:selected.entity[table="${type}"]`) : cy.nodes(`.entity[table="${type}"]`);
  return sel.map((n) => BY[type].get(n.data('pk'))).filter(Boolean);
}

const SUMMARY = {
  users(rows) {
    const ids = new Set(rows.map((r) => r.id));
    const ci = rows.flatMap((r) => rev('users', r.id, 'check_ins'));
    const rt = rows.flatMap((r) => rev('users', r.id, 'booth_ratings'));
    const recs = assigned(rows.flatMap((r) => rev('users', r.id, 'recommendation_scores')));
    const recV = recs.filter(recVisited);
    const votes = rows.flatMap((r) => rev('users', r.id, 'award_votes'));
    const gacha = rows.flatMap((r) => rev('users', r.id, 'gacha_coin_uses'));
    const cards = rows.map((r) => userCard(r.id)).filter(Boolean);
    const cells = cards.flatMap((c) => rev('bingo_cards', c.id, 'bingo_cells'));
    const surveys = rows.map((r) => survey(r.id)).filter(Boolean);
    const visitors = rows.filter((r) => rev('users', r.id, 'check_ins').length);
    const perUser = stats(visitors.map((r) => rev('users', r.id, 'check_ins').length));
    const byStrat = new Map();
    for (const r of recs) { const k = strategyOf(r); const v = byStrat.get(k) || [0, 0]; v[1]++; if (recVisited(r)) v[0]++; byStrat.set(k, v); }
    const attr = (k) => count(surveys.flatMap((x) => [].concat(x[k] ?? [])));
    return `<div class="tiles">
      ${tile('人数', fmtN(rows.length), `チェックインした人 ${fmtN(visitors.length)}（${pct(visitors.length, rows.length)}）`)}
      ${tile('チェックイン', fmtN(ci.length), `1人あたり 平均 ${fmtN(perUser.avg)} ・ 中央値 ${fmtN(perUser.med)} ・ 最大 ${fmtN(perUser.max)}`)}
      ${tile('評価', fmtN(rt.length), `チェックインの ${pct(rt.length, ci.length)} ・ コメント付き ${fmtN(rt.filter(hasComment).length)}`)}
      ${tile('推薦された枠', fmtN(recs.length), `推薦を受けた人 ${fmtN(new Set(recs.map((r) => r.user_id)).size)}`)}
      ${tile('推薦先を訪問', fmtN(recV.length), `推薦枠の ${pct(recV.length, recs.length)}（時刻は問わない）`)}
      ${tile('アワード投票', fmtN(votes.length), `投票した人 ${fmtN(new Set(votes.map((v) => v.user_id)).size)}`)}
      ${tile('ガチャコイン使用', fmtN(gacha.length), `使った人 ${fmtN(new Set(gacha.map((g) => g.user_id)).size)}`)}
      ${tile('ビンゴ達成マス', fmtN(cells.filter((c) => c.is_achieved === '1').length), `全 ${fmtN(cells.length)} マス ・ カード ${fmtN(cards.length)} 枚`)}
      ${tile('アンケート回答', fmtN(surveys.length), pct(surveys.length, rows.length))}
    </div>
    ${bars('推薦の戦略別：訪問した/推薦枠', byStrat)}
    ${bars('評価の★', count(rt.map((r) => `★${r.rating}`)), { show: (x) => x, sort: false, total: rt.length })}
    ${bars('役割', count(rows.map((r) => r.role)), { filterKey: 'role', total: rows.length })}
    ${bars('年代', attr('age_range'), { filterKey: 'age_range', total: surveys.length })}
    ${bars('職業', attr('occupation'), { filterKey: 'occupation', total: surveys.length })}
    ${bars('所属', attr('affiliation'), { filterKey: 'affiliation', total: surveys.length })}
    ${bars('性別', attr('gender'), { filterKey: 'gender', total: surveys.length })}
    ${bars('探索傾向', attr('exploration_disposition'), { filterKey: 'exploration_disposition', total: surveys.length })}
    ${bars('関心カテゴリ（アンケート）', attr('interest_categories'), { show: catName, filterKey: 'interest' })}
    ${bars('訪問したブースのカテゴリ', count(ci.map((c) => BY.booths.get(c.booth_id)?.category_id)), { show: catName, total: ci.length })}
    ${bars('よく訪問されたブース', count(ci.map((c) => c.booth_id)), { show: boothName, max: 10 })}
    ${bars('投票先のアワード', count(votes.map((v) => v.award_id)), { show: awardName })}
    ${bars('チェックインの時刻（時・保存値のまま）', new Map([...count(ci.map((c) => (c.checked_in_at || '').slice(11, 13))).entries()].sort()), { show: (h) => `${h}時`, sort: false, max: 24 })}
    ${bars('1人あたりの訪問数', new Map([...count(rows.map((r) => rev('users', r.id, 'check_ins').length)).entries()].sort((a, b) => a[0] - b[0])), { show: (n) => `${n}件`, sort: false, max: 60 })}
    ${ids.size === 0 ? '' : ''}`;
  },
  booths(rows) {
    const ci = rows.flatMap((r) => rev('booths', r.id, 'check_ins'));
    const rt = rows.flatMap((r) => rev('booths', r.id, 'booth_ratings'));
    const votes = rows.flatMap((r) => rev('booths', r.id, 'award_votes'));
    const recs = assigned(rows.flatMap((r) => rev('booths', r.id, 'recommendation_scores')));
    const recV = recs.filter(recVisited);
    const per = stats(rows.map((r) => rev('booths', r.id, 'check_ins').length));
    const avg = rt.length ? rt.reduce((a, b) => a + num(b.rating), 0) / rt.length : 0;
    const byStrat = new Map();
    for (const r of recs) { const k = strategyOf(r); const v = byStrat.get(k) || [0, 0]; v[1]++; if (recVisited(r)) v[0]++; byStrat.set(k, v); }
    const visitorSurveys = ci.map((c) => survey(c.user_id)).filter(Boolean);
    return `<div class="tiles">
      ${tile('ブース数', fmtN(rows.length))}
      ${tile('チェックイン', fmtN(ci.length), `ユニーク訪問者 ${fmtN(new Set(ci.map((c) => c.user_id)).size)}人`)}
      ${tile('1ブースあたり', fmtN(per.avg), `中央値 ${fmtN(per.med)} ・ 最大 ${fmtN(per.max)} ・ 最小 ${fmtN(per.min)}`)}
      ${tile('評価', fmtN(rt.length), `平均 ★${fmtN(avg)} ・ コメント ${fmtN(rt.filter(hasComment).length)}`)}
      ${tile('アワード得票', fmtN(votes.length), `投票した人 ${fmtN(new Set(votes.map((v) => v.user_id)).size)}`)}
      ${tile('推薦された枠', fmtN(recs.length), `うち訪問 ${fmtN(recV.length)}（${pct(recV.length, recs.length)}）`)}
      ${tile('ビンゴに出現', fmtN(rows.reduce((a, r) => a + rev('booths', r.id, 'bingo_cells').length, 0)), 'マス')}
    </div>
    ${bars('訪問数', new Map(rows.map((r) => [r.id, rev('booths', r.id, 'check_ins').length])), { show: boothName, max: 40 })}
    ${bars('評価の★', count(rt.map((r) => `★${r.rating}`)), { show: (x) => x, sort: false, total: rt.length })}
    ${bars('アワード別の得票', count(votes.map((v) => v.award_id)), { show: awardName })}
    ${bars('推薦の戦略別：訪問した/推薦枠', byStrat)}
    ${bars('カテゴリ', count(rows.map((r) => r.category_id)), { show: catName, filterKey: 'category' })}
    ${bars('訪問者の年代（延べ）', count(visitorSurveys.map((x) => x.age_range)), { total: visitorSurveys.length })}
    ${bars('訪問者の所属（延べ）', count(visitorSurveys.map((x) => x.affiliation)), { total: visitorSurveys.length })}`;
  },
  awards(rows) {
    const votes = rows.flatMap((r) => rev('awards', r.id, 'award_votes'));
    return `<div class="tiles">${tile('アワード数', fmtN(rows.length))}${tile('票数', fmtN(votes.length), `投票した人 ${fmtN(new Set(votes.map((v) => v.user_id)).size)}`)}${tile('得票したブース', fmtN(new Set(votes.map((v) => v.booth_id)).size))}</div>
      ${bars('アワード別の票数', count(votes.map((v) => v.award_id)), { show: awardName })}
      ${bars('得票の多いブース', count(votes.map((v) => v.booth_id)), { show: boothName, max: 15 })}`;
  },
  categories(rows) {
    const booths = rows.flatMap((r) => rev('categories', r.id, 'booths'));
    return `<div class="tiles">${tile('カテゴリ数', fmtN(rows.length))}${tile('ブース', fmtN(booths.length))}${tile('チェックイン', fmtN(booths.reduce((a, b) => a + rev('booths', b.id, 'check_ins').length, 0)))}</div>
      ${bars('カテゴリ別のブース数', new Map(rows.map((r) => [r.id, rev('categories', r.id, 'booths').length])), { show: catName })}
      ${bars('カテゴリ別のチェックイン', new Map(rows.map((r) => [r.id, rev('categories', r.id, 'booths').reduce((a, b) => a + rev('booths', b.id, 'check_ins').length, 0)])), { show: catName })}`;
  },
};

function renderSummary() {
  if ($('#summary').style.display === 'none' || !T[type]) return;
  const rows = scopeRows(); const E = ENTITY[type];
  const label = { filtered: '左の絞り込み結果', canvas: 'キャンバス上', selected: '選択中' }[summaryScope];
  $('#summary').innerHTML = `<div class="seg scope">${['filtered', 'canvas', 'selected'].map((k) =>
      `<button data-scope="${k}" class="${k === summaryScope ? 'active' : ''}">${{ filtered: '絞り込み結果', canvas: 'キャンバス上', selected: '選択中' }[k]}</button>`).join('')}</div>
    <p class="muted">${label}の${E.label} <b>${rows.length}</b>${E.unit} について数えています。元データを数えるだけで、値は変えていません。${type === 'users' ? '' : ''}</p>
    ${rows.length ? SUMMARY[type](rows) : '<p class="muted">対象がありません</p>'}`;
}
$('#summary').addEventListener('click', (e) => {
  const sc = e.target.closest('[data-scope]');
  if (sc) { summaryScope = sc.dataset.scope; renderSummary(); return; }
  const b = e.target.closest('.bar.click');
  if (b) { filterState[b.dataset.fk] = [b.dataset.fv]; summaryScope = 'filtered'; renderFilters(); refreshList(); }
});

load().catch((err) => {
  $('#meta').innerHTML = `<b style="color:#dc2626">読み込み失敗:</b> ${esc(err.message)}<br>python explorer/export_data.py を実行済みか確認`;
  console.error(err);
});
