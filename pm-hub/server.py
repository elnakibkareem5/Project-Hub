#!/usr/bin/env python3
"""Local test server for Project Hub. No installs needed (Python 3.8+).
Run:  python server.py     then open http://localhost:3000
Data is saved in data.json next to this file. Delete it to start fresh."""
import json, os, hmac, hashlib, base64, time, random, string, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, 'data.json')
ADMIN = os.environ.get('ADMIN_PASSWORD', 'admin123')
SECRET = (os.environ.get('SESSION_SECRET') or ADMIN).encode()
PORT = int(os.environ.get('PORT', 3000))
lock = threading.Lock()

class Err(Exception):
    def __init__(self, m, c=400): self.m, self.c = m, c

now = lambda: int(time.time() * 1000)
rid = lambda: ''.join(random.choices(string.ascii_lowercase + string.digits, k=7))
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b'=').decode()
unb64 = lambda s: base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))
mac = lambda s: b64(hmac.new(SECRET, s.encode(), hashlib.sha256).digest())
def sign(p): b = b64(json.dumps(p).encode()); return b + '.' + mac(b)
def unsign(t):
    try:
        b, g = str(t).split('.')
        if not hmac.compare_digest(g, mac(b)): return None
        p = json.loads(unb64(b)); return p if p['exp'] > now() else None
    except Exception: return None
session = lambda uid: sign({'uid': uid, 'exp': now() + 30 * 864000 * 100})

def init(): return {'settings': {'name': 'Project Hub', 'accent': '#ff5fa2'}, 'users': [], 'tasks': [], 'logs': []}
def load():
    try:
        with open(DB) as f: return json.load(f)
    except Exception: return init()
def save(s):
    with open(DB, 'w') as f: json.dump(s, f, indent=1)

def mkpw(pw):
    salt = os.urandom(16).hex()
    return {'salt': salt, 'hash': hashlib.scrypt(str(pw).encode(), salt=salt.encode(), n=16384, r=8, p=1, dklen=32).hex()}
def okpw(u, pw):
    if not u or not u.get('salt'): return False
    h = hashlib.scrypt(str(pw).encode(), salt=u['salt'].encode(), n=16384, r=8, p=1, dklen=32).hex()
    return hmac.compare_digest(h, u['hash'])
pub = lambda u: {k: v for k, v in u.items() if k not in ('salt', 'hash')}

def handle(b, h):
    import re
    a = b.get('action')
    with lock:
        s = load()
        if a == 'userlogin':
            u = next((u for u in s['users'] if u['username'] == str(b.get('username', '')).strip().lower()), None)
            if not okpw(u, b.get('password', '')): raise Err('Wrong username or password', 401)
            return {'token': session(u['id'])}
        is_admin = hmac.compare_digest(str(h.get('x-admin', '')), ADMIN)
        tk = unsign(h.get('x-token'))
        me = None if is_admin or not tk else next((u for u in s['users'] if u['id'] == tk['uid']), None)
        if a == 'login':
            if is_admin: return {'role': 'admin'}
            raise Err('Wrong admin password', 401)
        if not is_admin and not me: raise Err('Unauthorized', 401)
        dirty = True
        if is_admin:
            if a == 'addUser':
                un = str(b.get('username', '')).strip().lower()
                if not re.fullmatch(r'[a-z0-9_.-]{3,20}', un): raise Err('Username: 3-20 letters, numbers, _ . -')
                if len(str(b.get('password', ''))) < 4: raise Err('Password must be at least 4 characters')
                if any(u['username'] == un for u in s['users']): raise Err('Username already taken')
                s['users'].append({'id': rid(), 'username': un, 'name': str(b.get('name') or un)[:40], 'title': str(b.get('title', ''))[:40], **mkpw(b['password'])})
            elif a == 'resetPw':
                u = next((u for u in s['users'] if u['id'] == b.get('id')), None)
                if not u or len(str(b.get('password', ''))) < 4: raise Err('Password must be at least 4 characters')
                u.update(mkpw(b['password']))
            elif a == 'delUser':
                s['users'] = [u for u in s['users'] if u['id'] != b['id']]
                for t in s['tasks']:
                    if t.get('assignee') == b['id']: t['assignee'] = ''
            elif a == 'saveTask':
                nt = b['task']; t = next((x for x in s['tasks'] if x['id'] == nt.get('id')), None) if nt.get('id') else None
                if t: t.update(nt)
                else: t = {**nt, 'id': rid(), 'created': now()}; s['tasks'].append(t)
                if t.get('status') == 'done': t.setdefault('completedAt', now())
                else: t.pop('completedAt', None)
            elif a == 'delTask': s['tasks'] = [t for t in s['tasks'] if t['id'] != b['id']]
            elif a == 'settings':
                st = b['settings']; s['settings'].update({'name': str(st.get('name') or 'Project Hub')[:40], 'accent': st.get('accent')})
            else: dirty = False
        else:
            if a == 'setStatus':
                t = next((t for t in s['tasks'] if t['id'] == b.get('id') and t.get('assignee') == me['id']), None)
                if not t or b.get('status') not in ('todo', 'doing', 'done'): raise Err('Invalid')
                t['status'] = b['status']
                if b['status'] == 'done': t['completedAt'] = now()
                else: t.pop('completedAt', None)
            elif a == 'addLog':
                if not str(b.get('text', '')).strip(): raise Err('Empty log')
                try: hrs = max(0, float(b.get('hours') or 0))
                except ValueError: hrs = 0
                s['logs'].append({'id': rid(), 'user': me['id'], 'task': b.get('task') or '', 'text': str(b['text'])[:1000], 'hours': hrs, 'ts': now()})
            elif a == 'changePw':
                if not okpw(me, b.get('old', '')): raise Err('Current password is wrong')
                if len(str(b.get('new', ''))) < 4: raise Err('Password must be at least 4 characters')
                me.update(mkpw(b['new']))
            else: dirty = False
        if dirty: save(s)
        if is_admin: return {**s, 'users': [pub(u) for u in s['users']]}
        return {'settings': s['settings'], 'me': pub(me), 'users': [{'id': u['id'], 'name': u['name']} for u in s['users']],
                'tasks': [t for t in s['tasks'] if t.get('assignee') == me['id']], 'logs': [l for l in s['logs'] if l['user'] == me['id']]}

class H(BaseHTTPRequestHandler):
    def send(self, code, body, ctype='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.path.split('?')[0] in ('/', '/index.html'):
            with open(os.path.join(ROOT, 'index.html'), 'rb') as f: return self.send(200, f.read(), 'text/html; charset=utf-8')
        self.send(404, {'error': 'Not found'})
    def do_POST(self):
        if self.path.split('?')[0] != '/api': return self.send(404, {'error': 'Not found'})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
            self.send(200, handle(body, {k.lower(): v for k, v in self.headers.items()}))
        except Err as e: self.send(e.c, {'error': e.m})
        except Exception as e: self.send(500, {'error': str(e)})
    def log_message(self, *a): pass

if __name__ == '__main__':
    print(f'Project Hub running ->  http://localhost:{PORT}')
    print(f'Admin password: {ADMIN}  (add team members in the Team tab)')
    ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()
