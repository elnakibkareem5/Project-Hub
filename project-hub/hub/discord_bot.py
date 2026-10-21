"""Discord meeting logger. Reports voice-channel joins/leaves to Project Hub, which builds the meeting log.
Env: DISCORD_TOKEN, HUB_URL (e.g. https://your-app.azurewebsites.net), BOT_API_KEY (same value as on the website),
     DISCORD_CHANNEL_IDS (optional, comma separated: only log these voice channels)
Run:  pip install -r bot-requirements.txt && python discord_bot.py"""
import os, aiohttp, discord

HUB, KEY, TOKEN = os.environ['HUB_URL'].rstrip('/'), os.environ['BOT_API_KEY'], os.environ['DISCORD_TOKEN']
ONLY = {c.strip() for c in os.environ.get('DISCORD_CHANNEL_IDS', '').split(',') if c.strip()}
intents = discord.Intents.default(); intents.voice_states = True      # no privileged intents needed
client = discord.Client(intents=intents)

def wanted(ch): return ch is not None and (not ONLY or str(ch.id) in ONLY)

async def post(event, ch, member):
    body = {'event': event, 'channel_id': str(ch.id), 'channel': ch.name, 'discord_id': str(member.id), 'name': member.display_name}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(HUB + '/api/bot/voice', json=body, headers={'X-Bot-Key': KEY}, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200: print('Hub replied', r.status, await r.text())
    except Exception as e: print('Hub unreachable:', e)

@client.event
async def on_voice_state_update(member, before, after):
    if member.bot or before.channel == after.channel: return
    if wanted(before.channel): await post('leave', before.channel, member)
    if wanted(after.channel): await post('join', after.channel, member)

@client.event
async def on_ready():
    print('Logged in as', client.user)
    for g in client.guilds:                       # people already in a call when the bot starts
        for vc in g.voice_channels:
            if wanted(vc):
                for m in vc.members:
                    if not m.bot: await post('join', vc, m)

client.run(TOKEN)
