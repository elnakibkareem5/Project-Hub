// Vercel serverless function. Storage: Upstash Redis. Auth: admin password + member username/password.
const crypto = require('crypto');
const U = process.env.UPSTASH_REDIS_REST_URL || process.env.KV_REST_API_URL;
const T = process.env.UPSTASH_REDIS_REST_TOKEN || process.env.KV_REST_API_TOKEN;
const K = 'pmhub:state';
const SECRET = process.env.SESSION_SECRET || process.env.ADMIN_PASSWORD || 'change-me';
const init = () => ({ settings: { name: 'Project Hub', accent: '#ff5fa2' }, users: [], tasks: [], logs: [] });
const id = () => Math.random().toString(36).slice(2, 9);
const mac = b => crypto.createHmac('sha256', SECRET).update(b).digest('base64url');
const sign = p => { const b = Buffer.from(JSON.stringify(p)).toString('base64url'); return b + '.' + mac(b); };
const unsign = t => { try { const [b, g] = String(t).split('.'); if (g !== mac(b)) return null; const p = JSON.parse(Buffer.from(b, 'base64url')); return p.exp > Date.now() ? p : null; } catch { return null; } };
const hashPw = (p, salt) => crypto.scryptSync(String(p), salt, 32).toString('hex');
const mkPw = p => { const salt = crypto.randomBytes(16).toString('hex'); return { salt, hash: hashPw(p, salt) }; };
const okPw = (u, p) => { if (!u || !u.salt) return false; const a = Buffer.from(hashPw(p, u.salt), 'hex'), b = Buffer.from(u.hash, 'hex'); return a.length === b.length && crypto.timingSafeEqual(a, b); };
const pub = u => { const { salt, hash, ...r } = u; return r; };
async function load() {
  if (!U) {
    if (process.env.VERCEL) throw new Error('Database not connected. In Vercel: Storage > add Upstash Redis, connect it to this project, then redeploy.');
    return globalThis.__s || (globalThis.__s = init());
  }
  const r = await fetch(U + '/get/' + K, { headers: { Authorization: 'Bearer ' + T } }).then(r => r.json());
  if (r.error) throw new Error('Database error: ' + r.error);
  return r.result ? JSON.parse(r.result) : init();
}
async function save(s) {
  if (!U) { globalThis.__s = s; return; }
  await fetch(U + '/set/' + K, { method: 'POST', headers: { Authorization: 'Bearer ' + T }, body: JSON.stringify(s) });
}
const bad = (res, m, c = 400) => res.status(c).json({ error: m });
module.exports = async (req, res) => {
  try {
    const b = req.body || {}, a = b.action, s = await load();
    if (a === 'userlogin') {
      const u = s.users.find(u => u.username === String(b.username || '').trim().toLowerCase());
      if (!okPw(u, b.password)) return bad(res, 'Wrong username or password', 401);
      return res.json({ token: sign({ uid: u.id, exp: Date.now() + 30 * 864e5 }) });
    }
    const isAdmin = req.headers['x-admin'] === (process.env.ADMIN_PASSWORD || 'admin123');
    const tk = unsign(req.headers['x-token']);
    const me = !isAdmin && tk && s.users.find(u => u.id === tk.uid);
    if (a === 'login') return isAdmin ? res.json({ role: 'admin' }) : bad(res, 'Wrong admin password', 401);
    if (!isAdmin && !me) return bad(res, 'Unauthorized', 401);
    let dirty = true;
    if (isAdmin) {
      if (a === 'addUser') {
        const un = String(b.username || '').trim().toLowerCase();
        if (!/^[a-z0-9_.-]{3,20}$/.test(un)) return bad(res, 'Username: 3-20 letters, numbers, _ . -');
        if (String(b.password || '').length < 4) return bad(res, 'Password must be at least 4 characters');
        if (s.users.some(u => u.username === un)) return bad(res, 'Username already taken');
        s.users.push({ id: id(), username: un, name: String(b.name || un).slice(0, 40), title: String(b.title || '').slice(0, 40), ...mkPw(b.password) });
      } else if (a === 'resetPw') {
        const u = s.users.find(u => u.id === b.id);
        if (!u || String(b.password || '').length < 4) return bad(res, 'Password must be at least 4 characters');
        Object.assign(u, mkPw(b.password));
      } else if (a === 'delUser') {
        s.users = s.users.filter(u => u.id !== b.id);
        s.tasks.forEach(t => { if (t.assignee === b.id) t.assignee = ''; });
      } else if (a === 'saveTask') {
        let t = b.task.id && s.tasks.find(x => x.id === b.task.id);
        if (t) Object.assign(t, b.task); else { t = { ...b.task, id: id(), created: Date.now() }; s.tasks.push(t); }
        if (t.status === 'done') t.completedAt = t.completedAt || Date.now(); else delete t.completedAt;
      } else if (a === 'delTask') s.tasks = s.tasks.filter(t => t.id !== b.id);
      else if (a === 'settings') Object.assign(s.settings, { name: String(b.settings.name || 'Project Hub').slice(0, 40), accent: b.settings.accent });
      else dirty = false;
    } else {
      if (a === 'setStatus') {
        const t = s.tasks.find(t => t.id === b.id && t.assignee === me.id);
        if (!t || !['todo', 'doing', 'done'].includes(b.status)) return bad(res, 'Invalid');
        t.status = b.status;
        if (b.status === 'done') t.completedAt = Date.now(); else delete t.completedAt;
      } else if (a === 'addLog') {
        if (!String(b.text || '').trim()) return bad(res, 'Empty log');
        s.logs.push({ id: id(), user: me.id, task: b.task || '', text: String(b.text).slice(0, 1000), hours: Math.max(0, +b.hours || 0), ts: Date.now() });
      } else if (a === 'changePw') {
        if (!okPw(me, b.old)) return bad(res, 'Current password is wrong');
        if (String(b.new || '').length < 4) return bad(res, 'Password must be at least 4 characters');
        Object.assign(me, mkPw(b.new));
      } else dirty = false;
    }
    if (dirty) await save(s);
    res.json(isAdmin ? { ...s, users: s.users.map(pub) } : { settings: s.settings, me: pub(me), users: s.users.map(u => ({ id: u.id, name: u.name })), tasks: s.tasks.filter(t => t.assignee === me.id), logs: s.logs.filter(l => l.user === me.id) });
  } catch (e) { res.status(500).json({ error: e.message }); }
};
