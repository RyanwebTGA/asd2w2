# Discord server setup

`setup_server.py` builds the Untitled Isekai Discord server from `layout.json`:
roles (colours, permissions, order), categories, channels, and who can see or
post in each one. It never deletes anything, and running it again only changes
what's different, so you can edit `layout.json` and re-run it any time.

## Run it on your PC

1. Install Python 3 from python.org (tick "Add Python to PATH").
2. Give the bot's role **Administrator** and drag it to the top of
   Server Settings > Roles.
3. Open a terminal in this folder and set your bot token
   (Developer Portal > Bot > Reset Token). Never share it or commit it.
   - Windows (PowerShell): `$env:DISCORD_TOKEN = "your-token"`
   - macOS / Linux: `export DISCORD_TOKEN="your-token"`
4. See what would change: `python setup_server.py`
5. Make the changes: `python setup_server.py --apply`

## Editing the layout

- Add a channel: copy an existing line inside a category in `layout.json` and
  change the name. Types are `text`, `announcement`, `forum` and `voice`.
- Who can see or post: `overwrites` on a category apply to every channel in it.
  Overwrites on a channel are added on top.
- Permission names are Discord's (for example `SEND_MESSAGES`, `VIEW_CHANNEL`).
  Names starting with `@` are the presets at the top of the file.
- Roles are listed highest first. `"keep": true` means the script only moves that
  role and never changes its colour or permissions.
