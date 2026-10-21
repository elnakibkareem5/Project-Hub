# Project Hub (Python / Flask)

Team portal for a graduation project: tasks (Notion-style table + board), calendar with reminders, KPIs, groups (Active-Directory style),
online/offline presence, ideas with Reddit-style voting, roadmap, moderation (block / ban / break), language policy,
GitHub activity log, Discord meeting log.

## Themes
Every user (admin included) picks a dashboard style on first login and can change it any time with the **Theme** button in the sidebar:
Professional (light), Professional Dark, Retro Arcade, Terminal (CLI). The choice is saved on the account, so it follows the person to any device.
The admin accent colour (Settings) applies to the two Professional themes; Retro and Terminal have their own palettes.

## Run locally (2 minutes)
    pip install flask
    python app.py                 # http://localhost:3000  -> sign in: admin / admin123
Data is stored in `hub.db` (SQLite). Change the admin password right away: Settings -> Account.
Self-test: `python tests/test_backend.py`

## Deploy on Azure (App Service + PostgreSQL)
    az login
    az group create -n hub-rg -l westeurope
    az postgres flexible-server create -g hub-rg -n <unique-db-name> -u hubadmin -p '<DB-PASSWORD>' \
        --tier Burstable --sku-name Standard_B1ms --public-access 0.0.0.0 --database-name hub
    az webapp up -g hub-rg -n <unique-app-name> --runtime "PYTHON:3.11" --sku B1     # run inside this folder
    az webapp config set -g hub-rg -n <unique-app-name> \
        --startup-file "gunicorn --bind=0.0.0.0 --timeout 120 --workers 2 --threads 4 app:app"
    az webapp config appsettings set -g hub-rg -n <unique-app-name> --settings \
        DATABASE_URL="postgresql://hubadmin:<DB-PASSWORD>@<unique-db-name>.postgres.database.azure.com:5432/hub?sslmode=require" \
        ADMIN_USERNAME=<your-username> ADMIN_PASSWORD='<strong-password>' SECRET_KEY='<random-64-chars>' \
        SCM_DO_BUILD_DURING_DEPLOYMENT=true
Tables are created automatically on first start. Without `DATABASE_URL` the app uses SQLite in `/home/data` (fine for a demo; use PostgreSQL for real use).
Check `https://<unique-app-name>.azurewebsites.net/healthz` -> `ok`.

## GitHub
1. Fine-grained token with **Contents: read & write** on your repo -> app setting `GITHUB_TOKEN`.
2. Any long random string -> app setting `GITHUB_WEBHOOK_SECRET`.
3. Website: Settings -> GitHub: enter `owner/repo`, branch, log file (default `PROJECT_LOG.md`).
4. GitHub repo -> Settings -> Webhooks -> Add: Payload URL `https://<app>.azurewebsites.net/webhooks/github`, content type `application/json`,
   the same secret, events: Pushes, Pull requests, Issues.
Website changes are appended (within ~1 minute, as one commit starting with `[hub-log]`) to the log file with who/what/where;
every push, PR and issue shows in Activity with the author and files. Members enter their GitHub username in Account so commits are credited.
Moderation events (warnings, bans) and account changes are never written to GitHub.

## Discord bot (meeting log)
1. discord.com/developers -> New Application -> Bot -> copy the token. No privileged intents needed. Invite with the `bot` scope and "View Channels".
2. Set `BOT_API_KEY` on the website (any random string).
3. On an always-on machine (or a small Azure VM / second Web App):
       pip install -r bot-requirements.txt
       DISCORD_TOKEN=... HUB_URL=https://<app>.azurewebsites.net BOT_API_KEY=... python discord_bot.py
4. Members enter their Discord user ID (Developer Mode -> right-click yourself -> Copy User ID) in Account.
A meeting starts when the first person joins a voice channel and ends when the last leaves.

## Rules built in
* Language policy: curse words/insults in English, Arabic (and Arabizi), French, Spanish, German, Italian, Turkish, Hindi, Russian are blocked
  in ideas, comments, work logs, presence notes and names; the text is not saved. Violations 1-2 = warning, 3+ = automatic 24 h ban.
  Add more words in Policy. The admin is exempt.
* Tasks completed while a member is banned are NOT counted in KPIs (shown with a "not counted" tag); commits pushed while banned are flagged.
* Admin: warn, clear warnings, block, ban for N hours, lift ban, give a break (view-only), manage groups (Team -> Manage).
* Break = view-only and forced offline. Blocked/banned members only see a lock screen.
