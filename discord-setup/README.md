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

## Posts (embeds)

`messages.json` holds the info posts for each channel (welcome, rules, verify,
self-roles, links, open-a-ticket, staff guide). Edit the text there and run the
script again: posts are edited in place, so reactions on them keep working.
The links post waits until you fill in `GAME_LINK` and `GROUP_LINK` under
`placeholders` (full `https://` links).

`state.json` remembers the ID of every channel, role and post, so renaming
something in Discord never creates a duplicate. Commit it with the layout.

## Editing the layout

- Add a channel: copy an existing line inside a category in `layout.json` and
  change the name. Types are `text`, `announcement`, `forum` and `voice`.
- Naming style: categories are `「Name」`, channels are `「emoji」name`. Discord
  turns spaces in text channel names into dashes; the full-width brackets give
  the gap instead.
- Renaming in the layout: keep the old name in `"match"` so the script renames
  the channel instead of making a new one.
- Who can see or post: `overwrites` on a category apply to every channel in it.
  Overwrites on a channel are added on top.
- Permission names are Discord's (for example `SEND_MESSAGES`, `VIEW_CHANNEL`).
  Names starting with `@` are the presets at the top of the file.
- Roles are listed highest first. `"keep": true` means the script only moves that
  role and never changes its colour or permissions.
