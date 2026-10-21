"""Project Hub - Flask app. Run locally: python app.py   |   Azure: gunicorn app:app"""
import os, re, json, time, hmac, hashlib, base64, threading, urllib.request, urllib.error, urllib.parse
from datetime import timedelta
from flask import Flask, request, jsonify, session, send_from_directory, g
from werkzeug.local import LocalProxy
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
import db as DB, moderation

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=bool(os.environ.get('WEBSITE_SITE_NAME')), PERMANENT_SESSION_LIFETIME=timedelta(days=14), MAX_CONTENT_LENGTH=1 << 20)
TL = threading.local()
SEC, MIN, HOUR, DAY = 1000, 60000, 3600000, 86400000
PRI = ('Low', 'Medium', 'High'); STS = ('todo', 'doing', 'done'); STN = {'todo': 'Not started', 'doing': 'In progress', 'done': 'Completed'}
KPI_KEYS = ('n', 'rate', 'doing', 'over', 'ontime', 'hrs', 'online', 'uncounted')

def get_db():
    c = getattr(TL, 'c', None)
    if c: return c
    if 'db' not in g: g.db = DB.Conn()
    return g.db
db = LocalProxy(get_db)

@app.teardown_appcontext
def _close(e):
    c = g.pop('db', None)
    if c: c.close()

now = lambda: int(time.time() * 1000)
day = lambda ms: time.strftime('%Y-%m-%d', time.gmtime(ms / 1000))
def cfg(k, d=''):
    r = db.one('SELECT v FROM settings WHERE k=?', (k,)); return r['v'] if r else d
def setcfg(k, v): db.x('INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v', (k, str(v)))

def bootstrap():
    DB.init_db()
    with DB.Conn() as c:
        for k, v in (('secret', os.urandom(32).hex()), ('name', 'Project Hub'), ('accent', '#2383e2'), ('gh_last', '0'), ('kpis', 'n,rate,doing,over,ontime,hrs,online')):
            c.x('INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO NOTHING', (k, v))
        if not c.one('SELECT id FROM users WHERE is_admin=1'):
            pw = os.environ.get('ADMIN_PASSWORD', 'admin123')
            c.x('INSERT INTO users(username,name,pw_hash,is_admin,default_pw,created) VALUES(?,?,?,?,?,?) ON CONFLICT(username) DO NOTHING',
                (os.environ.get('ADMIN_USERNAME', 'admin').strip().lower(), 'Project Manager', generate_password_hash(pw), 1, int(pw == 'admin123'), now()))
        app.secret_key = os.environ.get('SECRET_KEY') or c.one("SELECT v FROM settings WHERE k='secret'")['v']

# ---------------------------------------------------------------- helpers
class Err(Exception):
    def __init__(s, m, c=400, **x): s.m, s.c, s.x = m, c, x
class Policy(Err): pass

def s_(b, k, n):
    return str(b.get(k) or '').strip()[:n]
def log(kind, summary, place, u=None, source='website', url='', actor=None):
    db.ins('activity', ts=now(), source=source, actor=actor or (u['username'] if u else 'system'), user_id=u['id'] if u else None, kind=kind, summary=summary[:300], place=place[:300], url=url[:300], synced=0)
def uname(i):
    r = db.one('SELECT name FROM users WHERE id=?', (i,)); return r['name'] if r else 'Deleted user'
def gname(i):
    r = db.one('SELECT name FROM ugroups WHERE id=?', (i,)); return r['name'] if r else 'Deleted group'
def asg_label(uid, gid): return gname(gid) + ' (group)' if gid else (uname(uid) if uid else 'Unassigned')
def gids(uid): return [r['group_id'] for r in db.q('SELECT group_id FROM group_members WHERE user_id=?', (uid,))]
def extra_words():
    try: return json.loads(cfg('badwords', '[]'))
    except Exception: return []
def bans_map():
    m = {}
    for r in db.q('SELECT user_id,starts,ends FROM bans'): m.setdefault(r['user_id'], []).append((r['starts'], r['ends']))
    return m
def restr(u):
    if u['is_admin']: return None
    t = now()
    if u['blocked']: return dict(type='blocked', reason=u['block_reason'], msg='Your account has been blocked by the project manager.')
    b = db.one('SELECT * FROM bans WHERE user_id=? AND ends>? ORDER BY ends DESC', (u['id'], t))
    if b: return dict(type='banned', until=b['ends'], reason=b['reason'], msg='You are banned until the time shown.')
    if u['break_until'] > t: return dict(type='break', until=u['break_until'], msg='You are on a break. The site is view-only until it ends.')
def go_offline(uid, at=None):
    db.x('UPDATE presence SET ended=? WHERE user_id=? AND ended IS NULL', (at or now(), uid)); db.x('UPDATE users SET online=0 WHERE id=?', (uid,))
def reap():
    for r in db.q('SELECT id,last_seen FROM users WHERE online=1 AND last_seen<?', (now() - 3 * MIN,)): go_offline(r['id'], r['last_seen'])

# -------- policy: language screening, 2 warnings then 24h ban
def screen(u, place, *texts):
    if u['is_admin']: return
    for tx in texts:
        w = moderation.check(tx or '', extra_words())
        if w: violate(u, place, w)
def violate(u, place, word):
    n, t = u['violations'] + 1, now(); masked = word[0] + '*' * (len(word) - 1)
    db.x('UPDATE users SET violations=? WHERE id=?', (n, u['id']))
    if n <= 2:
        db.ins('violations', user_id=u['id'], ts=t, place=place, matched=masked, action='warning %d/2' % n)
        log('violation', f"{u['name']} got warning {n}/2 for language in {place} ('{masked}')", 'Policy', u)
        raise Policy(f'Warning {n}/2: inappropriate language is not allowed. Your message was NOT posted. After 2 warnings you get a 24-hour ban and your tasks stop counting.', policy=True, banned=False)
    db.ins('violations', user_id=u['id'], ts=t, place=place, matched=masked, action='24h ban')
    db.ins('bans', user_id=u['id'], starts=t, ends=t + DAY, reason='Inappropriate language (automatic policy ban)', by_id=None, auto=1)
    go_offline(u['id'], t)
    log('violation', f"{u['name']} banned for 24h for language in {place} ('{masked}')", 'Policy', u)
    raise Policy('You are banned for 24 hours for repeated inappropriate language. Tasks completed during the ban do not count.', policy=True, banned=True)

# ---------------------------------------------------------------- KPI maths
def credit(t, adm):
    c = t['completed_by']; return c if c and c not in adm else t['assignee_id']
def counted(t, bm, adm):
    if t['status'] != 'done' or not t['completed_at']: return True
    uid = credit(t, adm)
    return not (uid and any(a <= t['completed_at'] <= z for a, z in bm.get(uid, [])))
def online_hours(rows, t, since):
    return sum(max(0, min(r['ended'] or t, t) - max(r['started'], since)) for r in rows) / HOUR
def kpi(tasks, bm, adm, logs, pres):
    t = now(); today = day(t); wk = t - 7 * DAY
    done = [x for x in tasks if x['status'] == 'done' and counted(x, bm, adm)]
    wd = [x for x in done if x['due'] and x['completed_at']]
    n = len(tasks)
    return dict(n=n, done=len(done), todo=sum(x['status'] == 'todo' for x in tasks), doing=sum(x['status'] == 'doing' for x in tasks),
                over=sum(x['status'] != 'done' and bool(x['due']) and x['due'] < today for x in tasks),
                rate=round(len(done) / n * 100) if n else 0, ontime=round(sum(day(x['completed_at']) <= x['due'] for x in wd) / len(wd) * 100) if wd else 100,
                uncounted=sum(x['status'] == 'done' and not counted(x, bm, adm) for x in tasks),
                hrs=round(sum(l['hours'] or 0 for l in logs if l['ts'] > wk), 1), online=round(online_hours(pres, t, wk), 1))

# ---------------------------------------------------------------- state
def pub_settings(u):
    st = dict(name=cfg('name'), accent=cfg('accent'), kpis=cfg('kpis').split(','))
    if u['is_admin']:
        st.update(github_repo=cfg('github_repo'), github_branch=cfg('github_branch', 'main'), github_path=cfg('github_path', 'PROJECT_LOG.md'),
                  gh_token=bool(os.environ.get('GITHUB_TOKEN')), gh_secret=bool(os.environ.get('GITHUB_WEBHOOK_SECRET')), bot_key=bool(os.environ.get('BOT_API_KEY')),
                  gh_error=cfg('gh_error'), gh_pending=db.one("SELECT COUNT(*) AS n FROM activity WHERE synced=0 AND source<>'github'")['n'],
                  webhook=request.host_url.rstrip('/') + '/webhooks/github', bot_url=request.host_url.rstrip('/') + '/api/bot/voice', badwords=extra_words())
    return st
def pme(u):
    d = {k: u[k] for k in ('id', 'username', 'name', 'title', 'is_admin', 'online', 'note', 'since', 'discord_id', 'github_login', 'default_pw', 'violations', 'theme')}
    d['groups'] = gids(u['id']); return d

def state(u):
    t = now(); r = restr(u); st = {'me': pme(u), 'settings': pub_settings(u)}
    if r:
        st['restricted'] = r
        if r['type'] != 'break': return st
    reap()
    if u['online']: db.x('UPDATE users SET last_seen=? WHERE id=?', (t, u['id']))
    adm_ = u['is_admin']; users = db.q('SELECT * FROM users ORDER BY name'); groups = db.q('SELECT * FROM ugroups ORDER BY name')
    gm = {}
    for m in db.q('SELECT * FROM group_members'): gm.setdefault(m['user_id'], []).append(m['group_id'])
    adm = {x['id'] for x in users if x['is_admin']}; bm = bans_map(); wk = t - 7 * DAY
    if adm_: tasks = db.q('SELECT * FROM tasks ORDER BY id DESC')
    else:
        mine = gm.get(u['id'], []); tasks = db.q('SELECT * FROM tasks WHERE assignee_id=?' + (' OR group_id IN (%s)' % ','.join('?' * len(mine)) if mine else '') + ' ORDER BY id DESC', [u['id']] + mine)
    logs = db.q('SELECT * FROM logs ' + ('' if adm_ else 'WHERE user_id=? ') + 'ORDER BY id DESC LIMIT 300', [] if adm_ else [u['id']])
    pres = db.q('SELECT * FROM presence WHERE (ended IS NULL OR ended>?)' + ('' if adm_ else ' AND user_id=?'), [wk] + ([] if adm_ else [u['id']]))
    for x in tasks:
        x['assignee'] = 'g:%d' % x['group_id'] if x['group_id'] else ('u:%d' % x['assignee_id'] if x['assignee_id'] else '')
        x['counted'] = counted(x, bm, adm)
    st['tasks'], st['logs'] = tasks, logs
    st['kpi'] = kpi(tasks, bm, adm, logs, pres)
    st['groups'] = [dict(g_, members=[uid for uid, gs in gm.items() if g_['id'] in gs]) for g_ in groups]
    out = []
    for x in users:
        state_ = 'blocked' if x['blocked'] else 'banned' if any(a <= t <= z for a, z in bm.get(x['id'], [])) else 'break' if x['break_until'] > t else 'active'
        d = dict(id=x['id'], name=x['name'], title=x['title'], is_admin=x['is_admin'], online=x['online'], note=x['note'], since=x['since'], groups=gm.get(x['id'], []), status=state_)
        if adm_:
            mg = gm.get(x['id'], []); mt = [k for k in tasks if k['assignee_id'] == x['id'] or k['completed_by'] == x['id'] or (k['group_id'] in mg and k['status'] != 'done')]
            d.update(username=x['username'], violations=x['violations'], break_until=x['break_until'], block_reason=x['block_reason'], discord_id=x['discord_id'], github_login=x['github_login'],
                     stats=kpi(mt, bm, adm, [l for l in logs if l['user_id'] == x['id']], [p for p in pres if p['user_id'] == x['id']]),
                     ban_until=max([z for a, z in bm.get(x['id'], []) if z > t] or [0]))
        out.append(d)
    st['users'] = out
    ideas = db.q('SELECT * FROM ideas ORDER BY id DESC LIMIT 300'); votes = db.q('SELECT * FROM votes'); cm = db.q('SELECT * FROM comments ORDER BY id')
    itask = {k['idea_id']: k for k in db.q('SELECT id,idea_id,status FROM tasks WHERE idea_id IS NOT NULL')}
    for i in ideas:
        v = [x for x in votes if x['idea_id'] == i['id']]
        i.update(score=sum(x['value'] for x in v), up=sum(x['value'] > 0 for x in v), down=sum(x['value'] < 0 for x in v), my=next((x['value'] for x in v if x['user_id'] == u['id']), 0),
                 comments=[dict(id=c['id'], user_id=c['user_id'], text=c['text'], ts=c['ts']) for c in cm if c['idea_id'] == i['id']], task=itask.get(i['id']))
    st['ideas'] = ideas
    act_ = db.q('SELECT * FROM activity ORDER BY id DESC LIMIT 250')
    st['activity'] = act_ if adm_ else [a for a in act_ if not a['kind'].startswith(('admin.', 'violation', 'account'))][:150]
    ms = db.q('SELECT * FROM meetings ORDER BY id DESC LIMIT 40'); at = db.q('SELECT * FROM attendance WHERE meeting_id IN (SELECT id FROM meetings ORDER BY id DESC LIMIT 40)')
    for m in ms: m['attendees'] = [a for a in at if a['meeting_id'] == m['id']]
    st['meetings'] = ms
    if adm_: st['violations'] = db.q('SELECT v.*, u.name AS user_name FROM violations v LEFT JOIN users u ON u.id=v.user_id ORDER BY v.id DESC LIMIT 60')
    return st

# ---------------------------------------------------------------- API plumbing
ACTS = {}
def act(name, role='user'):
    def d(f): ACTS[name] = (f, role); return f
    return d
LOCKED_OK = {'get', 'logout', 'setTheme'}; BREAK_OK = {'get', 'logout', 'accountUpdate', 'setTheme'}
FAILS = {}

def cur_user():
    uid = session.get('uid')
    if not uid: return None
    u = db.one('SELECT * FROM users WHERE id=?', (uid,))
    return u if u and session.get('sv') == u['pw_hash'][-10:] else None

@app.post('/api')
def api():
    if not request.is_json: return jsonify(error='JSON only'), 415
    b = request.get_json(silent=True) or {}; a = b.get('action'); f, role = ACTS.get(a, (None, None))
    if not f: return jsonify(error='Unknown action'), 404
    u = cur_user()
    try:
        if role != 'public':
            if not u: return jsonify(error='Please sign in'), 401
            if role == 'admin' and not u['is_admin']: raise Err('Admin only', 403)
            r = restr(u)
            if r and a not in (BREAK_OK if r['type'] == 'break' else LOCKED_OK): raise Err(r['msg'], 403, restricted=r)
        out = f(u, b); db.commit(); return jsonify(out if out is not None else {'ok': 1})
    except Policy as e:
        db.commit(); return jsonify(error=e.m, **e.x), e.c
    except Err as e:
        db.rollback(); return jsonify(error=e.m, **e.x), e.c
    except (KeyError, ValueError, TypeError) as e:
        db.rollback(); return jsonify(error='Bad request'), 400

@act('get')
def _(u, b): return state(u)
@act('login', 'public')
def _(u, b):
    un, pw = s_(b, 'username', 40).lower(), str(b.get('password') or ''); key = (request.remote_addr, un); t = now()
    FAILS[key] = [x for x in FAILS.get(key, []) if t - x < 10 * MIN]
    if len(FAILS[key]) >= 8: raise Err('Too many attempts. Try again in a few minutes.', 429)
    r = db.one('SELECT * FROM users WHERE username=?', (un,))
    if not r or not check_password_hash(r['pw_hash'], pw):
        FAILS[key].append(t); raise Err('Wrong username or password', 401)
    session.clear(); session['uid'] = r['id']; session['sv'] = r['pw_hash'][-10:]; session.permanent = True
@act('logout', 'public')
def _(u, b):
    if u: go_offline(u['id'])
    session.clear()

@act('accountUpdate')
def _(u, b):
    upd = {}
    if 'name' in b:
        name = s_(b, 'name', 40); screen(u, 'display name', name)
        if name: upd['name'] = name
    for k in ('discord_id', 'github_login'):
        if k in b:
            v = s_(b, k, 40)
            if not re.fullmatch(r'[\w.@-]*', v): raise Err('Invalid ' + k.replace('_', ' '))
            upd[k] = v
    nu = s_(b, 'username', 20).lower() if 'username' in b else u['username']
    if nu != u['username']:
        if not u['is_admin']: raise Err('Only the project manager can change usernames')
        if not re.fullmatch(r'[a-z0-9_.-]{3,20}', nu): raise Err('Username: 3-20 letters, numbers, _ . -')
        if db.one('SELECT id FROM users WHERE username=? AND id<>?', (nu, u['id'])): raise Err('Username already taken')
        upd['username'] = nu
    npw = str(b.get('new_password') or '')
    if npw and len(npw) < (8 if u['is_admin'] else 6): raise Err('Password must be at least %d characters' % (8 if u['is_admin'] else 6))
    if ('username' in upd or npw) and not check_password_hash(u['pw_hash'], str(b.get('current_password') or '')): raise Err('Current password is incorrect')
    if npw: upd['pw_hash'] = generate_password_hash(npw); upd['default_pw'] = 0
    if upd:
        db.x('UPDATE users SET ' + ','.join(k + '=?' for k in upd) + ' WHERE id=?', list(upd.values()) + [u['id']])
        if 'pw_hash' in upd: session['sv'] = upd['pw_hash'][-10:]
        if 'username' in upd or npw: log('account.update', f"{u['name']} changed their " + ' and '.join(x for x, y in (('username', 'username' in upd), ('password', npw)) if y), 'Account', u)

@act('setTheme')
def _(u, b):
    if b.get('theme') not in ('pro', 'dark', 'retro', 'cli'): raise Err('Unknown theme')
    db.x('UPDATE users SET theme=? WHERE id=?', (b['theme'], u['id']))

# ---------------- users, groups, moderation (admin)
def newpw(p):
    if len(str(p or '')) < 6: raise Err('Password must be at least 6 characters')
    return generate_password_hash(str(p))
@act('addUser', 'admin')
def _(u, b):
    un = s_(b, 'username', 20).lower()
    if not re.fullmatch(r'[a-z0-9_.-]{3,20}', un): raise Err('Username: 3-20 letters, numbers, _ . -')
    if db.one('SELECT id FROM users WHERE username=?', (un,)): raise Err('Username already taken')
    i = db.ins('users', username=un, name=s_(b, 'name', 40) or un, title=s_(b, 'title', 40), pw_hash=newpw(b.get('password')), created=now())
    for gid in b.get('groups') or []:
        if str(gid).isdigit(): db.x('INSERT INTO group_members(group_id,user_id) VALUES(?,?) ON CONFLICT DO NOTHING', (int(gid), i))
    log('admin.user', f'Added member {un}', 'Team', u)
def target(b):
    t = db.one('SELECT * FROM users WHERE id=?', (int(b['id']),))
    if not t: raise Err('User not found', 404)
    return t
@act('editUser', 'admin')
def _(u, b):
    t = target(b); un = s_(b, 'username', 20).lower() or t['username']
    if not re.fullmatch(r'[a-z0-9_.-]{3,20}', un): raise Err('Username: 3-20 letters, numbers, _ . -')
    if db.one('SELECT id FROM users WHERE username=? AND id<>?', (un, t['id'])): raise Err('Username already taken')
    db.x('UPDATE users SET username=?,name=?,title=?,discord_id=?,github_login=? WHERE id=?', (un, s_(b, 'name', 40) or un, s_(b, 'title', 40), s_(b, 'discord_id', 40), s_(b, 'github_login', 40), t['id']))
    log('admin.user', f"Edited member {t['username']}", 'Team', u)
@act('resetPw', 'admin')
def _(u, b):
    t = target(b)
    if t['is_admin']: raise Err('Use Account settings to change the admin password')
    db.x('UPDATE users SET pw_hash=? WHERE id=?', (newpw(b.get('password')), t['id'])); log('admin.user', f"Reset password for {t['username']}", 'Team', u)
@act('delUser', 'admin')
def _(u, b):
    t = target(b)
    if t['is_admin']: raise Err('The admin account cannot be removed')
    for q in ('UPDATE tasks SET assignee_id=NULL WHERE assignee_id=?', 'DELETE FROM group_members WHERE user_id=?', 'DELETE FROM votes WHERE user_id=?'): db.x(q, (t['id'],))
    db.x('DELETE FROM users WHERE id=?', (t['id'],)); log('admin.user', f"Removed member {t['username']}", 'Team', u)
@act('modUser', 'admin')
def _(u, b):
    t = target(b); op = b.get('op'); t0 = now(); why = s_(b, 'reason', 120)
    if t['is_admin']: raise Err('You cannot moderate the admin account')
    hrs = min(max(float(b.get('hours') or 24), 0.1), 24 * 365)
    if op == 'block': db.x('UPDATE users SET blocked=1,block_reason=? WHERE id=?', (why, t['id'])); go_offline(t['id'])
    elif op == 'unblock': db.x("UPDATE users SET blocked=0,block_reason='' WHERE id=?", (t['id'],))
    elif op == 'ban': db.ins('bans', user_id=t['id'], starts=t0, ends=t0 + int(hrs * HOUR), reason=why or 'Banned by the project manager', by_id=u['id']); go_offline(t['id'])
    elif op == 'unban': db.x('UPDATE bans SET ends=? WHERE user_id=? AND ends>?', (t0, t['id'], t0))
    elif op == 'break': db.x('UPDATE users SET break_until=? WHERE id=?', (t0 + int(hrs * HOUR), t['id'])); go_offline(t['id'])
    elif op == 'endbreak': db.x('UPDATE users SET break_until=0 WHERE id=?', (t['id'],))
    elif op == 'warn': db.x('UPDATE users SET violations=violations+1 WHERE id=?', (t['id'],)); db.ins('violations', user_id=t['id'], ts=t0, place='Manual', matched='-', action='manual warning')
    elif op == 'clearwarn': db.x('UPDATE users SET violations=0 WHERE id=?', (t['id'],))
    else: raise Err('Unknown moderation action')
    log('admin.' + op, f"{op} -> {t['name']}" + (f' ({why})' if why else '') + (f' for {hrs:g}h' if op in ('ban', 'break') else ''), 'Team', u)
@act('saveGroup', 'admin')
def _(u, b):
    name = s_(b, 'name', 40)
    if not name: raise Err('Group name required')
    try:
        if b.get('id'): db.x('UPDATE ugroups SET name=?,description=? WHERE id=?', (name, s_(b, 'description', 200), int(b['id'])))
        else: db.ins('ugroups', name=name, description=s_(b, 'description', 200))
    except Exception as e:
        if 'unique' in str(e).lower() or 'duplicate' in str(e).lower(): raise Err('A group with this name exists')
        raise
    log('admin.group', f'Saved group {name}', 'Team', u)
@act('delGroup', 'admin')
def _(u, b):
    i = int(b['id']); db.x('DELETE FROM group_members WHERE group_id=?', (i,)); db.x('UPDATE tasks SET group_id=NULL WHERE group_id=?', (i,)); db.x('DELETE FROM ugroups WHERE id=?', (i,)); log('admin.group', 'Deleted a group', 'Team', u)
@act('groupMember', 'admin')
def _(u, b):
    g_, m = int(b['group_id']), int(b['user_id'])
    if b.get('add'): db.x('INSERT INTO group_members(group_id,user_id) VALUES(?,?) ON CONFLICT DO NOTHING', (g_, m))
    else: db.x('DELETE FROM group_members WHERE group_id=? AND user_id=?', (g_, m))
    log('admin.group', f"{'Added' if b.get('add') else 'Removed'} {uname(m)} {'to' if b.get('add') else 'from'} {gname(g_)}", 'Team', u)

# ---------------- tasks
def parse_asg(v):
    v = str(v or '')
    if v[:2] == 'u:' and v[2:].isdigit() and db.one('SELECT id FROM users WHERE id=?', (int(v[2:]),)): return int(v[2:]), None
    if v[:2] == 'g:' and v[2:].isdigit() and db.one('SELECT id FROM ugroups WHERE id=?', (int(v[2:]),)): return None, int(v[2:])
    return None, None
def set_status(f, old, new, actor):
    f['status'] = new
    if new == 'done' and old != 'done': f['completed_at'], f['completed_by'] = now(), actor
    elif new != 'done': f['completed_at'], f['completed_by'] = None, None
@act('saveTask', 'admin')
def _(u, b):
    d = b['task']; f = {}; old = db.one('SELECT * FROM tasks WHERE id=?', (int(d['id']),)) if d.get('id') else None
    if d.get('id') and not old: raise Err('Task not found', 404)
    if 'title' in d: f['title'] = s_(d, 'title', 140) or 'Untitled'
    if 'description' in d: f['description'] = s_(d, 'description', 2000)
    if 'priority' in d and d['priority'] in PRI: f['priority'] = d['priority']
    if 'due' in d:
        due = s_(d, 'due', 10)
        if due and not re.fullmatch(r'\d{4}-\d\d-\d\d', due): raise Err('Bad date')
        f['due'] = due
    if 'assignee' in d: f['assignee_id'], f['group_id'] = parse_asg(d['assignee'])
    if d.get('status') in STS: set_status(f, old['status'] if old else 'todo', d['status'], u['id'])
    if old:
        if not f: return
        diff = []
        for k, v in f.items():
            if k in ('completed_at', 'completed_by') or old[k] == v: continue
            diff.append('assignee: %s -> %s' % (asg_label(old['assignee_id'], old['group_id']), asg_label(f.get('assignee_id'), f.get('group_id'))) if k == 'assignee_id' else 'status: %s -> %s' % (STN[old[k]], STN[v]) if k == 'status' else None if k == 'group_id' else f'{k}: {str(old[k])[:30]} -> {str(v)[:30]}')
        db.x('UPDATE tasks SET ' + ','.join(k + '=?' for k in f) + ' WHERE id=?', list(f.values()) + [old['id']])
        if [x for x in diff if x]: log('task.updated', f"Updated '{f.get('title', old['title'])}': " + '; '.join(x for x in diff if x), 'Website > Tasks', u)
    else:
        f.setdefault('title', 'Untitled'); f.update(created=now(), created_by=u['id'])
        if str(d.get('idea_id', '')).isdigit() and db.one('SELECT id FROM ideas WHERE id=?', (int(d['idea_id']),)): f['idea_id'] = int(d['idea_id'])
        db.ins('tasks', **f); log('task.created', f"Created task '{f['title']}' for {asg_label(f.get('assignee_id'), f.get('group_id'))}", 'Website > Tasks', u)
@act('delTask', 'admin')
def _(u, b):
    t = db.one('SELECT title FROM tasks WHERE id=?', (int(b['id']),))
    if t: db.x('DELETE FROM tasks WHERE id=?', (int(b['id']),)); db.x('DELETE FROM logs WHERE task_id=?', (int(b['id']),)); log('task.deleted', f"Deleted task '{t['title']}'", 'Website > Tasks', u)
def can_see(u, t): return u['is_admin'] or t['assignee_id'] == u['id'] or (t['group_id'] and t['group_id'] in gids(u['id']))
@act('setStatus')
def _(u, b):
    t = db.one('SELECT * FROM tasks WHERE id=?', (int(b['id']),))
    if not t or not can_see(u, t): raise Err('Task not found', 404)
    if b.get('status') not in STS: raise Err('Invalid status')
    f = {}; set_status(f, t['status'], b['status'], u['id'])
    db.x('UPDATE tasks SET ' + ','.join(k + '=?' for k in f) + ' WHERE id=?', list(f.values()) + [t['id']])
    if t['status'] != b['status']: log('task.status', f"'{t['title']}': {STN[t['status']]} -> {STN[b['status']]}", 'Website > Tasks', u)
@act('addLog')
def _(u, b):
    text = s_(b, 'text', 1000)
    if not text: raise Err('Write what you worked on')
    screen(u, 'work log', text); tid = int(b['task']) if str(b.get('task') or '').isdigit() else None
    if tid:
        t = db.one('SELECT * FROM tasks WHERE id=?', (tid,))
        if not t or not can_see(u, t): tid = None
    try: hrs = min(max(float(b.get('hours') or 0), 0), 24)
    except ValueError: hrs = 0
    db.ins('logs', user_id=u['id'], task_id=tid, text=text, hours=hrs, ts=now()); log('worklog', f"{u['name']} logged {hrs:g}h: {text[:90]}", 'Website > Work log', u)
@act('setPresence')
def _(u, b):
    t = now()
    if b.get('online'):
        note = s_(b, 'note', 80); screen(u, 'presence note', note); tid = int(b['task']) if str(b.get('task') or '').isdigit() else None
        if u['online']: db.x('UPDATE presence SET note=?,task_id=? WHERE user_id=? AND ended IS NULL', (note, tid, u['id'])); db.x('UPDATE users SET note=? WHERE id=?', (note, u['id']))
        else: db.ins('presence', user_id=u['id'], started=t, note=note, task_id=tid); db.x('UPDATE users SET online=1,note=?,since=?,last_seen=? WHERE id=?', (note, t, t, u['id']))
    else: go_offline(u['id'], t)

# ---------------- ideas (suggestions + poll)
@act('addIdea')
def _(u, b):
    title, body = s_(b, 'title', 120), s_(b, 'body', 2000)
    if not title: raise Err('Give your idea a title')
    screen(u, 'idea', title, body); i = db.ins('ideas', title=title, body=body, author_id=u['id'], created=now())
    db.x('INSERT INTO votes(idea_id,user_id,value) VALUES(?,?,1) ON CONFLICT DO NOTHING', (i, u['id'])); log('idea.created', f"{u['name']} suggested: {title}", 'Website > Ideas', u)
@act('vote')
def _(u, b):
    i, v = int(b['idea_id']), int(b.get('value') or 0)
    if not db.one('SELECT id FROM ideas WHERE id=?', (i,)): raise Err('Idea not found', 404)
    if v == 0: db.x('DELETE FROM votes WHERE idea_id=? AND user_id=?', (i, u['id']))
    else: db.x('INSERT INTO votes(idea_id,user_id,value) VALUES(?,?,?) ON CONFLICT(idea_id,user_id) DO UPDATE SET value=excluded.value', (i, u['id'], 1 if v > 0 else -1))
@act('addComment')
def _(u, b):
    text = s_(b, 'text', 500); i = int(b['idea_id'])
    if not text: raise Err('Write a comment')
    idea = db.one('SELECT title FROM ideas WHERE id=?', (i,))
    if not idea: raise Err('Idea not found', 404)
    screen(u, 'comment', text); db.ins('comments', idea_id=i, user_id=u['id'], text=text, ts=now()); log('idea.comment', f"{u['name']} commented on '{idea['title']}'", 'Website > Ideas', u)
@act('decideIdea', 'admin')
def _(u, b):
    i, st = int(b['id']), b.get('status'); idea = db.one('SELECT title FROM ideas WHERE id=?', (i,))
    if st not in ('open', 'accepted', 'rejected') or not idea: raise Err('Bad request')
    db.x('UPDATE ideas SET status=?,decided_by=? WHERE id=?', (st, u['id'], i)); log('idea.' + st, f"Idea '{idea['title']}' marked {st}", 'Website > Ideas', u)
@act('delIdea')
def _(u, b):
    i = int(b['id']); idea = db.one('SELECT * FROM ideas WHERE id=?', (i,))
    if not idea or (not u['is_admin'] and (idea['author_id'] != u['id'] or idea['status'] != 'open')): raise Err('Not allowed', 403)
    for q in ('DELETE FROM votes WHERE idea_id=?', 'DELETE FROM comments WHERE idea_id=?', 'UPDATE tasks SET idea_id=NULL WHERE idea_id=?', 'DELETE FROM ideas WHERE id=?'): db.x(q, (i,))
    log('idea.deleted', f"Deleted idea '{idea['title']}'", 'Website > Ideas', u)

# ---------------- admin settings, policy words, integrations
@act('saveSettings', 'admin')
def _(u, b):
    st = b['settings']; accent = s_(st, 'accent', 7); repo = s_(st, 'github_repo', 100); br = s_(st, 'github_branch', 60) or 'main'; path = s_(st, 'github_path', 100) or 'PROJECT_LOG.md'
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', accent): raise Err('Bad color')
    if repo and not re.fullmatch(r'[\w.-]+/[\w.-]+', repo): raise Err('GitHub repo must look like owner/repo')
    if not re.fullmatch(r'[\w./-]+', br) or not re.fullmatch(r'[\w./ -]+', path) or '..' in path: raise Err('Bad branch or path')
    for k, v in (('name', s_(st, 'name', 40) or 'Project Hub'), ('accent', accent), ('github_repo', repo), ('github_branch', br), ('github_path', path.lstrip('/')), ('kpis', ','.join(k for k in st.get('kpis', []) if k in KPI_KEYS))): setcfg(k, v)
@act('badWord', 'admin')
def _(u, b):
    w = s_(b, 'word', 40).lower(); ws = extra_words()
    if not w: raise Err('Enter a word')
    if b.get('op') == 'add' and w not in ws: ws.append(w)
    if b.get('op') == 'del': ws = [x for x in ws if x != w]
    setcfg('badwords', json.dumps(ws)); log('admin.policy', f"Policy word list {b.get('op')}: {len(ws)} custom words", 'Policy', u)
@act('meetingNote', 'admin')
def _(u, b): db.x('UPDATE meetings SET notes=? WHERE id=?', (s_(b, 'notes', 3000), int(b['id'])))
@act('syncNow', 'admin')
def _(u, b): return {'result': flush()}

# ---------------------------------------------------------------- GitHub: website -> repo log, repo -> website webhook
def gh(method, path, data=None):
    req = urllib.request.Request('https://api.github.com' + path, method=method, data=json.dumps(data).encode() if data else None,
                                 headers={'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'], 'Accept': 'application/vnd.github+json', 'User-Agent': 'project-hub', 'X-GitHub-Api-Version': '2022-11-28'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r: return r.status, json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read() or b'{}')
        except Exception: return e.code, {}
def flush():
    repo, token = cfg('github_repo'), os.environ.get('GITHUB_TOKEN')
    if not repo or not token: return 'GitHub is not configured (set the repo in Settings and GITHUB_TOKEN in the server environment).'
    rows = db.q("SELECT * FROM activity WHERE synced=0 AND source<>'github' ORDER BY id LIMIT 200")
    private = [r['id'] for r in rows if r['kind'].startswith(('admin.', 'violation', 'account'))]
    rows = [r for r in rows if r['id'] not in private]
    if private: db.x('UPDATE activity SET synced=1 WHERE id IN (%s)' % ','.join('?' * len(private)), private)
    if not rows: db.commit(); return 'Nothing new to send.'
    br, path = cfg('github_branch', 'main'), cfg('github_path', 'PROJECT_LOG.md'); url = f'/repos/{repo}/contents/{urllib.parse.quote(path)}'
    code, cur = gh('GET', url + '?ref=' + urllib.parse.quote(br))
    if code == 200: text, sha = base64.b64decode(cur['content']).decode('utf-8', 'replace'), cur['sha']
    elif code == 404: text, sha = '# Project activity log\n\nWritten automatically by Project Hub. Format: time (UTC) | who | what | where\n\n', None
    else: setcfg('gh_error', f"GitHub {code}: {cur.get('message', '')}"); db.commit(); return cfg('gh_error')
    text += ''.join('- %s | %s | %s | %s\n' % (time.strftime('%Y-%m-%d %H:%M', time.gmtime(r['ts'] / 1000)), r['actor'], r['summary'].replace('\n', ' '), r['place']) for r in rows)
    code, res = gh('PUT', url, {'message': '[hub-log] %d website events' % len(rows), 'content': base64.b64encode(text.encode()).decode(), 'branch': br, **({'sha': sha} if sha else {})})
    if code in (200, 201):
        db.x('UPDATE activity SET synced=1 WHERE id IN (%s)' % ','.join('?' * len(rows)), [r['id'] for r in rows]); setcfg('gh_error', ''); db.commit(); return 'Sent %d events to GitHub.' % len(rows)
    setcfg('gh_error', f"GitHub {code}: {res.get('message', '')}"); db.commit(); return cfg('gh_error')
def bg_flush():
    TL.c = DB.Conn()
    try: flush()
    except Exception as e:
        try: setcfg('gh_error', str(e)[:200]); TL.c.commit()
        except Exception: pass
    finally: TL.c.close(); TL.c = None

@app.after_request
def _after(resp):
    resp.headers.update({'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'same-origin',
        'Content-Security-Policy': "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'"})
    if request.path in ('/', '/index.html'): resp.headers['Cache-Control'] = 'no-cache'
    try:
        if request.path == '/api' and request.method == 'POST' and os.environ.get('GITHUB_TOKEN') and cfg('github_repo'):
            t = now()
            if t - int(cfg('gh_last', '0')) > MIN and db.one("SELECT id FROM activity WHERE synced=0 AND source<>'github' LIMIT 1"):
                c = db.x("UPDATE settings SET v=? WHERE k='gh_last' AND CAST(v AS BIGINT)<?", (str(t), t - MIN)); db.commit()
                if c.rowcount == 1: threading.Thread(target=bg_flush, daemon=True).start()
    except Exception: pass
    return resp

@app.post('/webhooks/github')
def github_hook():
    secret = os.environ.get('GITHUB_WEBHOOK_SECRET', '')
    if not secret: return 'GITHUB_WEBHOOK_SECRET is not set on the server', 503
    if not hmac.compare_digest(request.headers.get('X-Hub-Signature-256', ''), 'sha256=' + hmac.new(secret.encode(), request.get_data(), hashlib.sha256).hexdigest()): return 'bad signature', 401
    ev, p = request.headers.get('X-GitHub-Event', ''), request.get_json(silent=True) or {}; repo = (p.get('repository') or {}).get('full_name', ''); bm = bans_map()
    def add(actor, kind, summary, place, url, ts=None):
        u = db.one('SELECT * FROM users WHERE lower(github_login)=?', (str(actor).lower(),)); t = ts or now()
        if u and any(a <= t <= z for a, z in bm.get(u['id'], [])): summary += ' [not counted: member banned]'
        db.ins('activity', ts=t, source='github', actor=u['username'] if u else str(actor), user_id=u['id'] if u else None, kind=kind, summary=summary[:300], place=place[:300], url=url[:300], synced=1)
    if ev == 'push':
        br = str(p.get('ref', '')).split('/', 2)[-1]
        for c in (p.get('commits') or [])[:20]:
            msg = (c.get('message') or '').split('\n')[0]
            if msg.startswith('[hub-log]'): continue
            files = (c.get('added') or []) + (c.get('modified') or []) + (c.get('removed') or [])
            a = c.get('author') or {}
            add(a.get('username') or a.get('name') or 'unknown', 'github.commit', 'Commit: ' + msg, f"{repo} > {br} > " + ', '.join(files[:6]) + (f' (+{len(files) - 6} more)' if len(files) > 6 else ''), c.get('url', ''))
    elif ev == 'pull_request':
        pr, ac = p.get('pull_request') or {}, p.get('action')
        if ac in ('opened', 'closed', 'reopened'): add((p.get('sender') or {}).get('login', '?'), 'github.pr', f"PR #{pr.get('number')} {'merged' if pr.get('merged') else ac}: {pr.get('title', '')}", f"{repo} > {(pr.get('base') or {}).get('ref', '')}", pr.get('html_url', ''))
    elif ev == 'issues':
        i, ac = p.get('issue') or {}, p.get('action')
        if ac in ('opened', 'closed', 'reopened'): add((p.get('sender') or {}).get('login', '?'), 'github.issue', f"Issue #{i.get('number')} {ac}: {i.get('title', '')}", repo, i.get('html_url', ''))
    db.commit(); return 'ok'

# ---------------------------------------------------------------- Discord bot endpoint (voice meetings)
@app.post('/api/bot/voice')
def bot_voice():
    key = os.environ.get('BOT_API_KEY', '')
    if not key or not hmac.compare_digest(request.headers.get('X-Bot-Key', ''), key): return jsonify(error='unauthorized'), 401
    b = request.get_json(silent=True) or {}; ch, did, name, t = str(b.get('channel_id', '')), str(b.get('discord_id', '')), str(b.get('name', ''))[:60], now()
    if not ch or not did or b.get('event') not in ('join', 'leave'): return jsonify(error='bad request'), 400
    m = db.one('SELECT * FROM meetings WHERE channel_id=? AND ended IS NULL ORDER BY id DESC', (ch,)); usr = db.one('SELECT * FROM users WHERE discord_id=?', (did,))
    if b['event'] == 'join':
        if not m:
            mid = db.ins('meetings', channel_id=ch, channel=str(b.get('channel', ''))[:60], started=t); m = {'id': mid}
            log('meeting.start', f"Meeting started in #{b.get('channel', '')}", 'Discord', None, source='discord', actor='discord-bot')
        if not db.one('SELECT id FROM attendance WHERE meeting_id=? AND discord_id=? AND left_at IS NULL', (m['id'], did)):
            db.ins('attendance', meeting_id=m['id'], discord_id=did, name=(usr['name'] if usr else name), user_id=usr['id'] if usr else None, joined=t)
    elif m:
        db.x('UPDATE attendance SET left_at=? WHERE meeting_id=? AND discord_id=? AND left_at IS NULL', (t, m['id'], did))
        if not db.one('SELECT id FROM attendance WHERE meeting_id=? AND left_at IS NULL', (m['id'],)):
            db.x('UPDATE meetings SET ended=? WHERE id=?', (t, m['id'])); full = db.one('SELECT * FROM meetings WHERE id=?', (m['id'],))
            who = sorted({a['name'] for a in db.q('SELECT name FROM attendance WHERE meeting_id=?', (m['id'],))})
            log('meeting.end', f"Meeting in #{full['channel']} ended after {round((t - full['started']) / MIN)} min. Attendees: {', '.join(who)}", 'Discord', None, source='discord', actor='discord-bot')
    db.commit(); return jsonify(ok=1)

@app.get('/')
def index(): return send_from_directory('static', 'index.html')
@app.get('/healthz')
def healthz(): return 'ok'

bootstrap()
if __name__ == '__main__':
    print('Project Hub on http://localhost:%s  (admin / ADMIN_PASSWORD, default admin123)' % os.environ.get('PORT', 3000))
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 3000)), debug=False, threaded=True)
