import os, sys, json, hmac, hashlib, base64, time
os.environ.update(SQLITE_PATH=__import__('tempfile').mkdtemp() + '/t.db', GITHUB_WEBHOOK_SECRET='whsec', BOT_API_KEY='botkey', GITHUB_TOKEN='tok')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A
ok = 0
def check(name, cond, extra=''):
    global ok
    print(('PASS ' if cond else 'FAIL ') + name, extra if not cond else ''); ok += bool(cond)
class C:
    def __init__(s): s.c = A.app.test_client()
    def __call__(s, action, **d):
        r = s.c.post('/api', json={'action': action, **d}); return r.status_code, r.get_json()
adm, sara, omar = C(), C(), C()
check('default admin login', adm('login', username='admin', password='admin123')[0] == 200)
st = adm('get')[1]; check('default_pw flag', st['me']['default_pw'] == 1 and st['me']['is_admin'] == 1)
check('bad login', C()('login', username='admin', password='x')[0] == 401)
check('anon blocked', C()('get')[0] == 401)
check('add users', adm('addUser', username='sara', name='Sara', password='pass1234')[0] == 200 and adm('addUser', username='omar', name='Omar', password='pass1234')[0] == 200)
check('dup/short pw', adm('addUser', username='sara', password='pass1234')[0] == 400 and adm('addUser', username='x1', password='1')[0] == 400)
sid = [u for u in adm('get')[1]['users'] if u['username'] == 'sara'][0]['id']; oid = [u for u in adm('get')[1]['users'] if u['username'] == 'omar'][0]['id']
sara('login', username='sara', password='pass1234'); omar('login', username='omar', password='pass1234')
# groups + group tasks
check('group create', adm('saveGroup', name='Backend', description='API team')[0] == 200)
gid = adm('get')[1]['groups'][0]['id']
adm('groupMember', group_id=gid, user_id=sid, add=True); adm('groupMember', group_id=gid, user_id=oid, add=True)
check('group members', sorted(adm('get')[1]['groups'][0]['members']) == sorted([sid, oid]))
adm('saveTask', task={'title': 'Build API', 'description': 'REST', 'assignee': f'g:{gid}', 'due': '2099-01-01', 'priority': 'High', 'status': 'todo'})
adm('saveTask', task={'title': 'Solo task', 'assignee': f'u:{sid}', 'due': '2000-01-01'})
adm('saveTask', task={'title': 'Other task', 'assignee': ''})
ts = sara('get')[1]['tasks']; check('user sees own+group tasks only', sorted(t['title'] for t in ts) == ['Build API', 'Solo task'], [t['title'] for t in ts])
tid = [t for t in ts if t['title'] == 'Build API'][0]['id']
check('group member completes group task', sara('setStatus', id=tid, status='done')[0] == 200)
t = [t for t in omar('get')[1]['tasks'] if t['id'] == tid][0]; check('other member sees it done', t['status'] == 'done' and t['completed_by'] == sid and t['counted'])
check('cannot touch unassigned task', sara('setStatus', id=[t['id'] for t in adm('get')[1]['tasks'] if t['title'] == 'Other task'][0], status='done')[0] == 404)
k = sara('get')[1]['kpi']; check('kpi', k['n'] == 2 and k['done'] == 1 and k['rate'] == 50 and k['over'] == 1, k)
# presence + worklog
check('go online', sara('setPresence', online=True, note='Writing API docs')[0] == 200)
u = [u for u in adm('get')[1]['users'] if u['id'] == sid][0]; check('admin sees online', u['online'] == 1 and u['note'] == 'Writing API docs')
check('worklog', sara('addLog', text='Implemented login', hours=2.5, task=tid)[0] == 200 and sara('get')[1]['kpi']['hrs'] == 2.5)
A.db.x('UPDATE users SET last_seen=? WHERE id=?', (A.now() - 10 * 60000, sid)) if False else None
with A.DB.Conn() as c: c.x('UPDATE users SET last_seen=? WHERE id=?', (A.now() - 10 * 60000, sid))
check('presence reaped after silence', [u for u in adm('get')[1]['users'] if u['id'] == sid][0]['online'] == 0)
check('online hours counted', sara('get')[1]['kpi']['online'] >= 0)
# ideas
check('idea add', sara('addIdea', title='Add dark mode', body='Please')[0] == 200)
iid = adm('get')[1]['ideas'][0]['id']
omar('vote', idea_id=iid, value=1); idea = adm('get')[1]['ideas'][0]; check('votes', idea['score'] == 2 and idea['up'] == 2)
omar('vote', idea_id=iid, value=-1); check('vote change', adm('get')[1]['ideas'][0]['score'] == 0)
omar('vote', idea_id=iid, value=0); check('vote remove', adm('get')[1]['ideas'][0]['score'] == 1)
check('comment', omar('addComment', idea_id=iid, text='Good idea')[0] == 200 and len(adm('get')[1]['ideas'][0]['comments']) == 1)
check('only admin decides', omar('decideIdea', id=iid, status='accepted')[0] == 403 and adm('decideIdea', id=iid, status='accepted')[0] == 200)
adm('saveTask', task={'title': 'Implement dark mode', 'assignee': f'g:{gid}', 'idea_id': iid})
check('idea->task link', adm('get')[1]['ideas'][0]['task']['id'] is not None)
# policy
code, r = omar('addIdea', title='this is fucking stupid', body='x'); check('warning 1', code == 400 and r.get('policy') and 'Warning 1/2' in r['error'] and not r['banned'], r)
check('bad idea not saved', all('fucking' not in i['title'] for i in adm('get')[1]['ideas']))
code, r = omar('addComment', idea_id=iid, text='انت شرموط'); check('warning 2 (arabic)', code == 400 and 'Warning 2/2' in r['error'], r)
code, r = omar('addLog', text='ya kosomak'); check('3rd violation = 24h ban', code == 400 and r['banned'] is True, r)
st = omar('get')[1]; check('banned user locked out', 'restricted' in st and st['restricted']['type'] == 'banned' and 'tasks' not in st and abs(st['restricted']['until'] - A.now() - 86400000) < 5000)
check('banned cannot act', omar('setStatus', id=tid, status='doing')[0] == 403 and omar('addIdea', title='hi')[0] == 403)
check('admin sees violations', len(adm('get')[1]['violations']) == 3 and adm('get')[1]['users'][[u['id'] for u in adm('get')[1]['users']].index(oid)]['status'] == 'banned')
adm('saveTask', task={'title': 'Omar solo', 'assignee': f'u:{oid}', 'status': 'todo'})
omt = [t for t in adm('get')[1]['tasks'] if t['title'] == 'Omar solo'][0]['id']
adm('saveTask', task={'id': omt, 'status': 'done'})   # admin completes it on his behalf while banned
t = [t for t in adm('get')[1]['tasks'] if t['id'] == omt][0]; check('task done during ban NOT counted', t['counted'] is False)
ost = [u for u in adm('get')[1]['users'] if u['id'] == oid][0]['stats']; check('stats exclude it', ost['done'] == 0 and ost['uncounted'] == 1, ost)
check('unban', adm('modUser', id=oid, op='unban')[0] == 200 and 'restricted' not in omar('get')[1])
t = [t for t in adm('get')[1]['tasks'] if t['id'] == omt][0]; check('still uncounted after ban ends (window kept)', t['counted'] is False)
adm('modUser', id=oid, op='clearwarn'); check('clear warnings', omar('get')[1]['me']['violations'] == 0)
check('clean text passes', omar('addComment', idea_id=iid, text='Totally fine, assess the class')[0] == 200)
custom = adm('badWord', op='add', word='blorf'); code, r = omar('addComment', idea_id=iid, text='what a blorf'); check('custom admin word', code == 400 and 'Warning 1/2' in r['error'])
adm('badWord', op='del', word='blorf')
# admin exempt from filter
check('admin exempt', adm('addIdea', title='shit happens')[0] == 200)
# break + block
adm('modUser', id=oid, op='break', hours=2); st = omar('get')[1]
check('break = view only', st['restricted']['type'] == 'break' and 'tasks' in st and omar('addLog', text='work')[0] == 403 and omar('setPresence', online=True)[0] == 403)
adm('modUser', id=oid, op='endbreak'); check('end break', omar('addLog', text='back at work')[0] == 200)
adm('modUser', id=oid, op='block', reason='testing'); check('blocked', omar('get')[1]['restricted']['type'] == 'blocked' and omar('addLog', text='x')[0] == 403)
adm('modUser', id=oid, op='unblock'); check('unblocked', 'restricted' not in omar('get')[1])
check('cannot moderate admin', adm('modUser', id=1, op='ban')[0] == 400 or adm('modUser', id=1, op='ban')[0] == 400)
check('member cannot use admin actions', sara('addUser', username='evil', password='abcdef')[0] == 403 and sara('modUser', id=oid, op='ban')[0] == 403)
check('member cannot change username', sara('accountUpdate', username='newname')[0] == 400)
check('member changes password', sara('accountUpdate', current_password='pass1234', new_password='newpass99')[0] == 200 and sara('get')[0] == 200 and C()('login', username='sara', password='newpass99')[0] == 200)
check('wrong current pw', sara('accountUpdate', current_password='nope', new_password='abcdefgh')[0] == 400)
# admin account change
check('admin weak pw rejected', adm('accountUpdate', current_password='admin123', new_password='short')[0] == 400)
check('admin changes username+password', adm('accountUpdate', username='pm_boss', current_password='admin123', new_password='Str0ng!pass')[0] == 200 and adm('get')[0] == 200)
n = C(); check('old creds dead, new work', n('login', username='admin', password='admin123')[0] == 401 and n('login', username='pm_boss', password='Str0ng!pass')[0] == 200 and n('get')[1]['me']['default_pw'] == 0)
# themes
check('theme default empty -> chooser', sara('get')[1]['me']['theme'] == '' and adm('get')[1]['me']['theme'] == '')
check('set theme', all(sara('setTheme', theme=x)[0] == 200 for x in ('pro', 'dark', 'retro', 'cli')) and sara('get')[1]['me']['theme'] == 'cli')
check('bad theme rejected', sara('setTheme', theme='neon')[0] == 400)
check('theme is per user', adm('setTheme', theme='retro')[0] == 200 and sara('get')[1]['me']['theme'] == 'cli' and adm('get')[1]['me']['theme'] == 'retro')
# rate limit
r = C(); [r('login', username='zzz', password='bad') for _ in range(8)]; check('login rate limit', r('login', username='zzz', password='bad')[0] == 429)
# webhook
wc = A.app.test_client()
def hook(ev, body, secret='whsec'):
    raw = json.dumps(body).encode(); sig = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return wc.post('/webhooks/github', data=raw, headers={'X-Hub-Signature-256': sig, 'X-GitHub-Event': ev, 'Content-Type': 'application/json'}).status_code
sara('accountUpdate', github_login='SaraGH', discord_id='111')
push = {'ref': 'refs/heads/main', 'repository': {'full_name': 'team/proj'}, 'commits': [
    {'message': 'Fix login bug\n\nlong', 'author': {'username': 'saragh', 'name': 'S'}, 'added': [], 'modified': ['src/auth.py', 'src/db.py'], 'removed': [], 'url': 'http://x/c1'},
    {'message': '[hub-log] 3 website events', 'author': {'username': 'bot'}, 'modified': ['PROJECT_LOG.md']}]}
check('webhook bad signature', hook('push', push, 'wrong') == 401)
check('webhook push ok', hook('push', push) == 200)
check('webhook PR + issue', hook('pull_request', {'action': 'closed', 'repository': {'full_name': 'team/proj'}, 'sender': {'login': 'saragh'}, 'pull_request': {'number': 7, 'merged': True, 'title': 'Auth', 'base': {'ref': 'main'}, 'html_url': 'u'}}) == 200 and hook('issues', {'action': 'opened', 'repository': {'full_name': 'team/proj'}, 'sender': {'login': 'x'}, 'issue': {'number': 2, 'title': 'Bug'}}) == 200)
acts = [a for a in sara('get')[1]['activity'] if a['source'] == 'github']
check('github events logged w/ who+where', len(acts) == 3 and any(a['actor'] == 'sara' and 'src/auth.py' in a['place'] and 'team/proj > main' in a['place'] for a in acts) and not any('hub-log' in a['summary'] for a in acts), acts)
# banned member's commit flagged
adm('modUser', id=sid, op='ban', hours=1)
hook('push', {'ref': 'refs/heads/dev', 'repository': {'full_name': 'team/proj'}, 'commits': [{'message': 'sneaky commit', 'author': {'username': 'saragh'}, 'modified': ['a.py']}]})
check('commit by banned member flagged', any('not counted' in a['summary'] for a in adm('get')[1]['activity']))
adm('modUser', id=sid, op='unban')
check('member sees no moderation entries in feed', not any(a['kind'].startswith(('admin.', 'violation')) for a in sara('get')[1]['activity']) and any(a['kind'].startswith('admin.') for a in adm('get')[1]['activity']))
# website -> github flush (mock API)
calls = []
def fake(method, path, data=None):
    calls.append((method, path, data))
    return (404, {'message': 'Not Found'}) if method == 'GET' else (201, {})
A.gh = fake
adm('saveSettings', settings={'name': 'Team Hub', 'accent': '#112233', 'github_repo': 'team/proj', 'github_branch': 'main', 'github_path': 'PROJECT_LOG.md', 'kpis': ['n', 'rate']})
res = adm('syncNow')[1]['result']; put = [c for c in calls if c[0] == 'PUT'][0]; body = base64.b64decode(put[2]['content']).decode()
check('flush sends website events', res.startswith('Sent') and put[2]['message'].startswith('[hub-log]') and 'Created task' in body, res)
check('flush hides moderation/account events', 'warning' not in body and 'banned' not in body and 'changed their' not in body and 'kosomak' not in body, body)
check('github-origin events not echoed back', 'Fix login bug' not in body)
check('second flush has nothing', adm('syncNow')[1]['result'] == 'Nothing new to send.')
A.gh = lambda m, p, d=None: (409, {'message': 'conflict'})
adm('saveTask', task={'title': 'another'}); check('flush error surfaced', 'GitHub 409' in adm('syncNow')[1]['result'] and 'GitHub 409' in adm('get')[1]['settings']['gh_error'])
st = adm('get')[1]['settings']; check('settings saved + kpis customizable', st['name'] == 'Team Hub' and st['kpis'] == ['n', 'rate'] and st['webhook'].endswith('/webhooks/github'))
check('bad repo rejected', adm('saveSettings', settings={'name': 'x', 'accent': '#112233', 'github_repo': 'bad repo!'})[0] == 400)
# discord bot
bot = A.app.test_client(); H = {'X-Bot-Key': 'botkey'}
def v(ev, did, name='X', ch='42'): return bot.post('/api/bot/voice', json={'event': ev, 'channel_id': ch, 'channel': 'Standup', 'discord_id': did, 'name': name}, headers=H).status_code
check('bot unauthorized', bot.post('/api/bot/voice', json={}, headers={'X-Bot-Key': 'x'}).status_code == 401)
check('bot join/leave', v('join', '111', 'sara#1') == 200 and v('join', '999', 'Guest') == 200 and v('leave', '111') == 200)
ms = adm('get')[1]['meetings']; check('meeting open w/ attendees', len(ms) == 1 and ms[0]['ended'] is None and len(ms[0]['attendees']) == 2)
v('leave', '999'); ms = adm('get')[1]['meetings']
check('meeting closed + mapped to member', ms[0]['ended'] and {a['name'] for a in ms[0]['attendees']} == {'Sara', 'Guest'} and any(a['user_id'] == sid for a in ms[0]['attendees']))
check('meeting logged in feed', any(a['kind'] == 'meeting.end' for a in sara('get')[1]['activity']))
check('meeting note', adm('meetingNote', id=ms[0]['id'], notes='Decided on schema')[0] == 200 and adm('get')[1]['meetings'][0]['notes'] == 'Decided on schema')
# delete user/group cleanup
check('delete group & user', adm('delGroup', id=gid)[0] == 200 and adm('delUser', id=oid)[0] == 200 and adm('delUser', id=1)[0] == 400)
check('logout', sara('logout')[0] == 200 and sara('get')[0] == 401)
print(f'\n{ok} checks passed')
