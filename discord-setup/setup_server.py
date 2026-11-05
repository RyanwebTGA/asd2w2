#!/usr/bin/env python3
"""Build a Discord server's roles and channels from layout.json.

    python3 setup_server.py            # dry run: only shows what would change
    python3 setup_server.py --apply    # makes the changes

The bot token is read from the DISCORD_TOKEN environment variable. Give the bot
Administrator for the run (Discord only lets a bot grant permissions it has
itself), and drag its role above every role listed in the layout.

Running it again is safe: anything that already matches the layout is left
alone, so it only creates or edits what is missing or different. Nothing is
ever deleted; channels and roles that aren't in the layout are listed at the end.

Standard library only (Python 3.8+).
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/RyanwebTGA/asd2w2, 1.0)"

# Bit positions from https://discord.com/developers/docs/topics/permissions
PERMISSIONS = {
    "CREATE_INSTANT_INVITE": 0, "KICK_MEMBERS": 1, "BAN_MEMBERS": 2,
    "ADMINISTRATOR": 3, "MANAGE_CHANNELS": 4, "MANAGE_GUILD": 5,
    "ADD_REACTIONS": 6, "VIEW_AUDIT_LOG": 7, "PRIORITY_SPEAKER": 8,
    "STREAM": 9, "VIEW_CHANNEL": 10, "SEND_MESSAGES": 11,
    "SEND_TTS_MESSAGES": 12, "MANAGE_MESSAGES": 13, "EMBED_LINKS": 14,
    "ATTACH_FILES": 15, "READ_MESSAGE_HISTORY": 16, "MENTION_EVERYONE": 17,
    "USE_EXTERNAL_EMOJIS": 18, "VIEW_GUILD_INSIGHTS": 19, "CONNECT": 20,
    "SPEAK": 21, "MUTE_MEMBERS": 22, "DEAFEN_MEMBERS": 23,
    "MOVE_MEMBERS": 24, "USE_VAD": 25, "CHANGE_NICKNAME": 26,
    "MANAGE_NICKNAMES": 27, "MANAGE_ROLES": 28, "MANAGE_WEBHOOKS": 29,
    "MANAGE_GUILD_EXPRESSIONS": 30, "USE_APPLICATION_COMMANDS": 31,
    "REQUEST_TO_SPEAK": 32, "MANAGE_EVENTS": 33, "MANAGE_THREADS": 34,
    "CREATE_PUBLIC_THREADS": 35, "CREATE_PRIVATE_THREADS": 36,
    "USE_EXTERNAL_STICKERS": 37, "SEND_MESSAGES_IN_THREADS": 38,
    "USE_EMBEDDED_ACTIVITIES": 39, "MODERATE_MEMBERS": 40,
    "VIEW_CREATOR_MONETIZATION_ANALYTICS": 41, "USE_SOUNDBOARD": 42,
    "CREATE_GUILD_EXPRESSIONS": 43, "CREATE_EVENTS": 44,
    "USE_EXTERNAL_SOUNDS": 45, "SEND_VOICE_MESSAGES": 46,
    "SET_VOICE_CHANNEL_STATUS": 48, "SEND_POLLS": 49, "USE_EXTERNAL_APPS": 50,
    "PIN_MESSAGES": 51, "BYPASS_SLOWMODE": 52,
}

CHANNEL_TYPES = {"text": 0, "voice": 2, "category": 4, "announcement": 5, "forum": 15}
TYPE_LABEL = {0: "text", 2: "voice", 4: "category", 5: "announcement", 13: "stage", 15: "forum", 16: "media"}
TEXT_LIKE = {0, 5, 15, 16}       # names are lowercase-with-dashes; listed above voice in a category
VOICE_LIKE = {2, 13}
CONVERTIBLE = {0, 5}             # Discord can switch text <-> announcement in place
SPEC_MATCHES = {0: {0, 5}, 5: {0, 5}, 2: {2}, 4: {4}, 15: {15}}
SUPPRESS_JOIN_NOTIFICATION_REPLIES = 1 << 3   # guild system_channel_flags


class DiscordError(Exception):
    pass


class Discord:
    def __init__(self, token, reason=None):
        self.token = token
        self.reason = reason

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        for attempt in range(8):
            req = urllib.request.Request(API + path, data=data, method=method)
            req.add_header("Authorization", "Bot " + self.token)
            req.add_header("User-Agent", USER_AGENT)
            if data is not None:
                req.add_header("Content-Type", "application/json")
            if method != "GET" and self.reason:
                req.add_header("X-Audit-Log-Reason", urllib.parse.quote(self.reason))
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    if resp.headers.get("X-RateLimit-Remaining") == "0":
                        time.sleep(float(resp.headers.get("X-RateLimit-Reset-After", "1")))
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as err:
                raw = err.read()
                if err.code == 429:
                    try:
                        wait = float(json.loads(raw).get("retry_after", 1))
                    except ValueError:
                        wait = 2.0
                    time.sleep(wait + 0.25)
                    continue
                # A POST that hit a 5xx may still have gone through, so don't resend it
                # (re-running the script picks up whatever was created).
                if err.code >= 500 and method != "POST" and attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise DiscordError("%s %s failed: HTTP %s %s" % (
                    method, path, err.code, raw.decode("utf-8", "replace")))
            except (urllib.error.URLError, OSError) as err:
                if method == "GET" and attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise DiscordError("%s %s failed: %s (run the script again to continue)" % (method, path, err))
        raise DiscordError("%s %s: still rate limited after several retries" % (method, path))

    def get_or_none(self, path):
        try:
            return self.call("GET", path)
        except DiscordError as err:
            if "HTTP 404" in str(err):
                return None
            raise


# ---------------------------------------------------------------- helpers

def resolve_perms(names, presets, seen=()):
    value = 0
    for name in names:
        if name.startswith("@"):
            key = name[1:]
            if key not in presets:
                sys.exit("Unknown permission preset %s" % name)
            if key in seen:
                sys.exit("Permission preset %s includes itself" % name)
            value |= resolve_perms(presets[key], presets, seen + (key,))
        elif name in PERMISSIONS:
            value |= 1 << PERMISSIONS[name]
        else:
            sys.exit("Unknown permission %s" % name)
    return value


def perm_names(value):
    return [name for name, bit in PERMISSIONS.items() if value >> bit & 1]


def parse_color(color):
    if not color:
        return 0
    return int(color.lstrip("#"), 16)


def role_color(role):
    colors = role.get("colors") or {}
    return colors.get("primary_color", role.get("color", 0)) or 0


def norm(name, ctype=None):
    """Compare names the way Discord stores them (text names are lowercased, spaces become dashes)."""
    n = name.replace("️", "").strip()
    if ctype in TEXT_LIKE:
        n = n.replace(" ", "-")
    return n.casefold()


def rank(role):
    """Sort key: higher = higher in the role list. Equal positions: the older (lower id) role wins."""
    return (role["position"], -int(role["id"]))


def fill_placeholders(value, fills):
    if isinstance(value, str):
        for key, text in fills.items():
            if text:
                value = value.replace("{%s}" % key, text)
        return value
    if isinstance(value, list):
        return [fill_placeholders(v, fills) for v in value]
    if isinstance(value, dict):
        return {k: fill_placeholders(v, fills) for k, v in value.items()}
    return value


def unfilled_placeholders(value):
    return sorted(set(re.findall(r"\{([A-Z_]+)\}", json.dumps(value, ensure_ascii=False))))


def comparable_message(msg):
    def embed(e):
        return {
            "title": (e.get("title") or "").strip(),
            "description": (e.get("description") or "").strip(),
            "url": e.get("url") or "",
            "color": e.get("color") or 0,
            "fields": [((f.get("name") or "").strip(), (f.get("value") or "").strip(), bool(f.get("inline")))
                       for f in e.get("fields") or []],
            "footer": ((e.get("footer") or {}).get("text") or "").strip(),
            "thumbnail": (e.get("thumbnail") or {}).get("url") or "",
            "image": (e.get("image") or {}).get("url") or "",
            "author": ((e.get("author") or {}).get("name") or "").strip(),
        }
    return ((msg.get("content") or "").strip(), [embed(e) for e in msg.get("embeds") or []])


def label(ch):
    ctype = ch["type"]
    return ("#" + ch["name"]) if ctype in TEXT_LIKE else ch["name"]


# ---------------------------------------------------------------- main

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="make the changes (default is a dry run)")
    parser.add_argument("--layout", default=os.path.join(here, "layout.json"), help="layout file to use")
    args = parser.parse_args()

    with open(args.layout, encoding="utf-8") as fh:
        layout = json.load(fh)

    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        sys.exit("Set the DISCORD_TOKEN environment variable to your bot token first.")
    if token.lower().startswith("bot "):
        token = token[4:]

    apply = args.apply
    api = Discord(token, layout.get("audit_log_reason"))
    # IDs of everything the script manages, so renames made in Discord don't cause duplicates
    state_path = os.path.join(os.path.dirname(os.path.abspath(args.layout)), "state.json")
    try:
        with open(state_path, encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}
    state.setdefault("roles", {})
    state.setdefault("channels", {})
    gid = layout["guild_id"]
    presets = layout.get("permission_presets", {})
    changes = []
    warnings = []

    def change(text):
        changes.append(text)
        print(("  " if apply else "  would ") + text)

    me = api.call("GET", "/users/@me")
    guild = api.call("GET", "/guilds/%s" % gid)
    roles = api.call("GET", "/guilds/%s/roles" % gid)
    channels = api.call("GET", "/guilds/%s/channels" % gid)
    member = api.call("GET", "/guilds/%s/members/%s" % (gid, me["id"]))

    print("%s on server: %s" % ("APPLYING" if apply else "DRY RUN", guild["name"]))

    roles_by_id = {r["id"]: r for r in roles}
    bot_role = next((r for r in roles if (r.get("tags") or {}).get("bot_id") == me["id"]), None)
    bot_top = max((roles_by_id[rid] for rid in member["roles"] if rid in roles_by_id), key=rank, default=None)
    if bot_role is None or bot_top is None:
        sys.exit("Couldn't find the bot's role. Re-invite the bot with the invite link.")
    bot_admin = any(int(roles_by_id[rid]["permissions"]) >> PERMISSIONS["ADMINISTRATOR"] & 1
                    for rid in member["roles"] if rid in roles_by_id)
    if apply and not bot_admin:
        sys.exit("Stopped before changing anything: give the bot's role (%s) Administrator for the run. "
                 "Without it Discord hides private channels from the bot and refuses some permissions."
                 % bot_top["name"])

    # ------------------------------------------------------------ roles
    print("\nRoles")
    # other bots' roles (Bloxlink, Carl-bot, ...) go where the layout lists "@bots"
    other_bots = sorted((r for r in roles if r["managed"] and r["id"] != bot_role["id"]
                         and (r.get("tags") or {}).get("bot_id")), key=rank, reverse=True)
    has_bots_slot = any(spec["name"] == "@bots" for spec in layout["roles"])
    role_ids = {"@everyone": gid, "@bot": bot_role["id"], "@bots": [r["id"] for r in other_bots]}
    claimed_roles = set()
    order = []          # layout roles top -> bottom (ids, "new:..." in a dry run)
    blocked = []        # existing roles the bot can't touch because they're above it

    def find_role(names):
        wanted = {norm(n) for n in names}
        for r in roles:
            if r["managed"] or r["id"] == gid or r["id"] in claimed_roles:
                continue
            if norm(r["name"]) in wanted:
                return r
        return None

    if has_bots_slot:
        blocked += [r["name"] for r in other_bots if rank(r) >= rank(bot_top)]
    found_roles = []
    renamed_roles = set()
    for spec in layout["roles"]:
        if spec["name"] == "@bots":
            found_roles.append(None)
            continue
        found = find_role([spec["name"]] + spec.get("match", []))
        if not found:
            known = next((r for r in roles if r["id"] == state["roles"].get(spec["name"])
                          and r["id"] not in claimed_roles and not r["managed"]), None)
            if known:
                found = known
                renamed_roles.add(known["id"])
                warnings.append("Role %r was renamed in Discord to %r; keeping that name. Change it in "
                                "layout.json to match." % (spec["name"], known["name"]))
        if found:
            claimed_roles.add(found["id"])
            if rank(found) >= rank(bot_top):
                blocked.append(found["name"])
        found_roles.append(found)

    if blocked:
        msg = ("The bot's role (%s) must be dragged above %s in Server Settings > Roles."
               % (bot_top["name"], ", ".join(blocked)))
        if apply:
            sys.exit("Stopped before changing anything. " + msg)
        warnings.append(msg)

    for spec, found in zip(layout["roles"], found_roles):
        name = spec["name"]
        if name == "@bots":
            order.extend(r["id"] for r in other_bots)
            continue
        if spec.get("keep"):
            if not found:
                sys.exit("Role %r is marked keep but doesn't exist on the server." % name)
            role_ids[name] = found["id"]
            state["roles"][name] = found["id"]
            order.append(found["id"])
            continue
        want = {
            "name": name,
            "permissions": str(resolve_perms(spec.get("permissions", []), presets)),
            "colors": {"primary_color": parse_color(spec.get("color"))},
            "hoist": bool(spec.get("hoist")),
            "mentionable": bool(spec.get("mentionable")),
        }
        if not found:
            change("create role %s (colour %s%s%s)" % (
                name, spec.get("color") or "none",
                ", shown separately" if want["hoist"] else "",
                ", mentionable" if want["mentionable"] else ""))
            if apply:
                created = api.call("POST", "/guilds/%s/roles" % gid, want)
                roles.append(created)
                rid = created["id"]
            else:
                rid = "new:" + name
        else:
            rid = found["id"]
            diff, notes = {}, []
            if found["name"] != name and found["id"] not in renamed_roles:
                diff["name"] = name
                notes.append("rename from %s" % found["name"])
            if found["permissions"] != want["permissions"]:
                diff["permissions"] = want["permissions"]
                notes.append("permissions")
            if role_color(found) != want["colors"]["primary_color"]:
                diff["colors"] = want["colors"]
                notes.append("colour")
            for key in ("hoist", "mentionable"):
                if bool(found[key]) != want[key]:
                    diff[key] = want[key]
                    notes.append(key)
            if diff:
                change("update role %s: %s" % (found["name"], ", ".join(notes)))
                if apply:
                    api.call("PATCH", "/guilds/%s/roles/%s" % (gid, rid), diff)
        role_ids[name] = rid
        if not rid.startswith("new:"):
            state["roles"][name] = rid
        order.append(rid)

    if apply:
        roles = api.call("GET", "/guilds/%s/roles" % gid)
    current = {r["id"]: r for r in roles}
    existing = [rid for rid in order if rid in current]
    by_rank = sorted(existing, key=lambda rid: rank(current[rid]), reverse=True)
    if len(existing) != len(order) or by_rank != existing:
        names = [current[rid]["name"] if rid in current else rid[4:] for rid in order]
        if has_bots_slot and not other_bots:
            names.insert(next(i for i, sp in enumerate(layout["roles"]) if sp["name"] == "@bots"), "(other bots)")
        change("order roles: %s" % " > ".join(names))
        if apply:
            # Renumber every role below the bot: layout roles first (in layout order),
            # then any other roles in their current order. A bot can only move roles
            # below its own highest role, so all positions must stay under it.
            top = next(r for r in roles if r["id"] == bot_top["id"])
            below = [r for r in roles if r["id"] != gid and rank(r) < rank(top)]
            others = sorted((r for r in below if r["id"] not in order), key=rank, reverse=True)
            # other bots' roles (verification bots etc.) stay above the layout roles so they
            # can still hand them out; any other unlisted roles go underneath
            if has_bots_slot:
                sequence = order + [r["id"] for r in others]
            else:
                sequence = ([r["id"] for r in others if r["managed"]] + order
                            + [r["id"] for r in others if not r["managed"]])
            n = len(sequence)
            payload = [{"id": rid, "position": n - i} for i, rid in enumerate(sequence)]
            if top["position"] <= n:
                # Discord stacked new roles on the same position instead of shifting the
                # bot's role up. Try renumbering with the bot's role kept on top.
                above = sorted((r for r in roles if r["id"] != gid and rank(r) >= rank(top)), key=rank)
                payload += [{"id": r["id"], "position": n + 1 + i} for i, r in enumerate(above)]
                try:
                    api.call("PATCH", "/guilds/%s/roles" % gid, payload)
                except DiscordError as err:
                    sys.exit("Stopped: Discord won't let the bot renumber roles (%s). In Server Settings > "
                             "Roles, drag any role (for example Moderator) to a new spot and save, so "
                             "Discord renumbers them, then run this again." % err)
            else:
                api.call("PATCH", "/guilds/%s/roles" % gid, payload)
            roles = api.call("GET", "/guilds/%s/roles" % gid)
            live = {r["id"]: r for r in roles}
            if sorted(order, key=lambda rid: rank(live[rid]), reverse=True) != order:
                warnings.append("Discord didn't keep the role order exactly; run the script again to retry.")

    # @everyone's server-wide permissions (e.g. no View Channel = verification gate)
    if "everyone_permissions" in layout:
        everyone = next(r for r in roles if r["id"] == gid)
        want = str(resolve_perms(layout["everyone_permissions"], presets))
        if everyone["permissions"] != want:
            removed = perm_names(int(everyone["permissions"]) & ~int(want))
            added = perm_names(int(want) & ~int(everyone["permissions"]))
            change("update @everyone permissions (%s)" % ", ".join(
                ["-" + n for n in removed] + ["+" + n for n in added]))
            if apply:
                api.call("PATCH", "/guilds/%s/roles/%s" % (gid, gid), {"permissions": want})

    # other bots' own roles: give each only the permissions it needs
    for bot_name, perms in layout.get("bot_role_permissions", {}).items():
        match = next((r for r in other_bots if norm(r["name"]) == norm(bot_name)), None)
        if match is None:
            warnings.append("Bot role %r isn't on the server (is the bot added?)." % bot_name)
            continue
        want = str(resolve_perms(perms, presets))
        if match["permissions"] != want:
            change("set %s's permissions to: %s" % (match["name"], ", ".join(perm_names(int(want)))))
            if apply:
                api.call("PATCH", "/guilds/%s/roles/%s" % (gid, match["id"]), {"permissions": want})

    # ------------------------------------------------------------ channels
    print("\nChannels")
    claimed = set()
    name_to_id = {}
    placement = []      # (channel id, parent id or None, index) in layout order

    def resolve_overwrites(spec_ow):
        out = {}
        for key, value in (spec_ow or {}).items():
            if key not in role_ids:
                sys.exit("Overwrite for unknown role %r (add it to roles, or use @everyone / @bot)." % key)
            ids = role_ids[key] if isinstance(role_ids[key], list) else [role_ids[key]]
            for rid in ids:
                out[rid] = (resolve_perms(value.get("allow", []), presets),
                            resolve_perms(value.get("deny", []), presets))
        return out

    def merge(base, extra):
        out = dict(base)
        for rid, (allow, deny) in extra.items():
            cur_allow, cur_deny = out.get(rid, (0, 0))
            out[rid] = ((cur_allow | allow) & ~deny, (cur_deny | deny) & ~allow)
        return out

    def current_overwrites(ch):
        return {o["id"]: (int(o["allow"]), int(o["deny"]))
                for o in ch.get("permission_overwrites", []) if o["type"] == 0}

    def overwrite_payload(want, ch=None):
        payload = [{"id": rid, "type": 0, "allow": str(a), "deny": str(d)}
                   for rid, (a, d) in want.items() if not rid.startswith("new:")]
        if ch:   # keep any per-member overwrites someone added by hand
            payload += [o for o in ch.get("permission_overwrites", []) if o["type"] == 1]
        return payload

    def clean(ow):
        return {k: v for k, v in ow.items() if v != (0, 0)}

    def find_channel(names, ctype):
        allowed = SPEC_MATCHES[ctype]
        for ch in channels:
            if ch["id"] in claimed or ch["type"] not in allowed:
                continue
            if any(norm(ch["name"], ch["type"]) == norm(n, ch["type"]) for n in names):
                return ch
        return None

    def ensure(spec, ctype, parent_id, want_ow, where):
        name = spec["name"]
        found = find_channel([name] + spec.get("match", []), ctype)
        renamed = False
        if not found:
            known = next((c for c in channels if c["id"] == state["channels"].get(name)
                          and c["id"] not in claimed and c["type"] in SPEC_MATCHES[ctype]), None)
            if known:
                found, renamed = known, True
                warnings.append("%s was renamed in Discord to %s; keeping that name. Change it in "
                                "layout.json to match." % (name, label(known)))
        tags = [{"name": t["name"], "moderated": bool(t.get("moderated")), "emoji_name": t.get("emoji")}
                for t in spec.get("tags", [])]
        if not found:
            body = {"name": name, "type": ctype,
                    "permission_overwrites": overwrite_payload(want_ow)}
            if parent_id:
                body["parent_id"] = parent_id
            if "topic" in spec:
                body["topic"] = spec["topic"]
            if "slowmode" in spec:
                body["rate_limit_per_user"] = spec["slowmode"]
            if "user_limit" in spec:
                body["user_limit"] = spec["user_limit"]
            if tags:
                body["available_tags"] = tags
            change("create %s %s%s" % (TYPE_LABEL[ctype], name, where))
            if apply:
                created = api.call("POST", "/guilds/%s/channels" % gid, body)
                channels.append(created)
                claimed.add(created["id"])
                state["channels"][name] = created["id"]
                return created["id"]
            return "new:" + name
        claimed.add(found["id"])
        state["channels"][name] = found["id"]
        diff, notes = {}, []
        if not renamed and norm(found["name"], found["type"]) != norm(name, found["type"]):
            diff["name"] = name
            notes.append("rename to %s" % name)
        if found["type"] != ctype:
            if found["type"] in CONVERTIBLE and ctype in CONVERTIBLE:
                diff["type"] = ctype
                notes.append("make it %s" % TYPE_LABEL[ctype])
            else:
                warnings.append("%s is a %s channel, layout wants %s; left as is"
                                % (label(found), TYPE_LABEL.get(found["type"]), TYPE_LABEL[ctype]))
        if ctype != 4 and found.get("parent_id") != parent_id:
            diff["parent_id"] = None if parent_id is None or parent_id.startswith("new:") else parent_id
            notes.append(("move to " + where[4:]) if where else "move out of its category")
        if "topic" in spec and (found.get("topic") or "") != spec["topic"]:
            diff["topic"] = spec["topic"]
            notes.append("topic")
        if "slowmode" in spec and found.get("rate_limit_per_user", 0) != spec["slowmode"]:
            diff["rate_limit_per_user"] = spec["slowmode"]
            notes.append("slowmode %ss" % spec["slowmode"])
        if "user_limit" in spec and found.get("user_limit", 0) != spec["user_limit"]:
            diff["user_limit"] = spec["user_limit"]
            notes.append("user limit %s" % spec["user_limit"])
        if clean(current_overwrites(found)) != clean(want_ow):
            diff["permission_overwrites"] = overwrite_payload(want_ow, found)
            notes.append("permissions")
        if tags and found["type"] == 15:
            have = {t["name"] for t in found.get("available_tags", [])}
            missing = [t for t in tags if t["name"] not in have]
            if missing:
                diff["available_tags"] = found.get("available_tags", []) + missing
                notes.append("tags")
        if diff:
            change("update %s: %s" % (label(found), ", ".join(notes)))
            if apply:
                api.call("PATCH", "/channels/%s" % found["id"], diff)
        return found["id"]

    for ci, cat in enumerate(layout["categories"]):
        base = resolve_overwrites(cat.get("overwrites"))
        cat_id = ensure(cat, 4, None, base, "")
        placement.append((cat_id, None, ci))
        where = " in %s" % cat["name"]
        for i, spec in enumerate(cat["channels"]):
            ctype = CHANNEL_TYPES[spec["type"]]
            want = merge(base, resolve_overwrites(spec.get("overwrites")))
            cid = ensure(spec, ctype, cat_id, want, where)
            name_to_id[spec["name"]] = cid
            placement.append((cid, cat_id, i))

    # order: categories top to bottom, and channels inside each category
    if apply:
        channels = api.call("GET", "/guilds/%s/channels" % gid)
    by_id = {c["id"]: c for c in channels}

    def current_order(ids):
        live = [i for i in ids if i in by_id]
        return sorted(live, key=lambda i: (by_id[i]["position"], int(by_id[i]["id"])))

    groups = {}
    for cid, parent, _ in placement:
        ctype = by_id[cid]["type"] if cid in by_id else None
        bucket = "voice" if ctype in VOICE_LIKE else "text"
        groups.setdefault((parent, bucket if parent else "cat"), []).append(cid)
    reorder = any(
        any(i.startswith("new:") for i in ids) or current_order(ids) != ids
        for ids in groups.values())
    if reorder:
        change("put categories and channels in layout order")
        if apply:
            api.call("PATCH", "/guilds/%s/channels" % gid,
                     [{"id": cid, "position": idx} for cid, _, idx in placement])

    # ------------------------------------------------------------ server settings
    system = layout.get("system_channel")
    if system:
        sid = name_to_id.get(system)
        if sid is None:
            sys.exit("system_channel %r isn't a channel in the layout." % system)
        body = {}
        if guild.get("system_channel_id") != sid:
            body["system_channel_id"] = sid
            change("send join messages to %s" % system)
        if layout.get("hide_join_wave_button"):
            flags = guild.get("system_channel_flags", 0)
            if not flags & SUPPRESS_JOIN_NOTIFICATION_REPLIES:
                body["system_channel_flags"] = flags | SUPPRESS_JOIN_NOTIFICATION_REPLIES
                change("hide the 'Wave to say hi' button on join messages")
        if body and apply and not sid.startswith("new:"):
            api.call("PATCH", "/guilds/%s" % gid, body)

    # ------------------------------------------------------------ messages (embeds) from messages.json
    msg_path = os.path.join(os.path.dirname(os.path.abspath(args.layout)), "messages.json")
    if os.path.exists(msg_path):
        print("\nMessages")
        with open(msg_path, encoding="utf-8") as fh:
            msg_layout = json.load(fh)
        fills = msg_layout.get("placeholders", {})
        state.setdefault("messages", {})
        for ch_name, posts in msg_layout.get("channels", {}).items():
            cid = name_to_id.get(ch_name)
            if cid is None:
                warnings.append("messages.json has posts for %r, which isn't a channel in the layout." % ch_name)
                continue
            if cid.startswith("new:"):
                change("post %d message(s) in %s" % (len(posts), ch_name))
                continue
            ids = list(state["messages"].get(ch_name, []))
            kept = []
            for i, post in enumerate(posts):
                body = {"content": post.get("content", ""), "embeds": post.get("embeds", [])}
                body = fill_placeholders(body, fills)
                mid = ids[i] if i < len(ids) else None
                missing = unfilled_placeholders(body)
                if missing:
                    warnings.append("Skipped message %d in %s until you fill in %s in messages.json."
                                    % (i + 1, ch_name, ", ".join(missing)))
                    kept.append(mid)
                    continue
                live = api.get_or_none("/channels/%s/messages/%s" % (cid, mid)) if mid else None
                if live is None:
                    change("post message %d in %s" % (i + 1, ch_name))
                    if apply:
                        body["allowed_mentions"] = {"parse": []}
                        mid = api.call("POST", "/channels/%s/messages" % cid, body)["id"]
                elif comparable_message(live) != comparable_message(body):
                    change("update message %d in %s" % (i + 1, ch_name))
                    if apply:
                        body["allowed_mentions"] = {"parse": []}
                        api.call("PATCH", "/channels/%s/messages/%s" % (cid, mid), body)
                kept.append(mid)
            state["messages"][ch_name] = kept

    # ------------------------------------------------------------ report
    known = set(order) | {gid, bot_role["id"]}
    extra_roles = [r["name"] for r in roles if r["id"] not in known and not r["managed"]]
    extra_channels = [label(c) for c in channels if c["id"] not in claimed]
    if extra_roles:
        print("\nRoles not in the layout (left alone): " + ", ".join(extra_roles))
    if extra_channels:
        print("Channels not in the layout (left alone): " + ", ".join(extra_channels))
    if not bot_admin:
        warnings.append("The bot isn't an Administrator. Private channels need View Channel for the "
                        "bot's role, or it can't see them on the next run.")
    for w in warnings:
        print("\nWARNING: " + w)

    if apply:
        # only remember things that are still in the layout
        layout_channels = {c["name"] for c in layout["categories"]}
        layout_channels |= {ch["name"] for c in layout["categories"] for ch in c["channels"]}
        state["channels"] = {k: v for k, v in state["channels"].items() if k in layout_channels}
        state["roles"] = {k: v for k, v in state["roles"].items()
                          if k in {r["name"] for r in layout["roles"]}}
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    if not changes:
        print("\nEverything already matches the layout. Nothing to change.")
    elif apply:
        print("\nDone: %d change(s) made." % len(changes))
    else:
        print("\nDry run: nothing was changed. Run with --apply to make these %d change(s)." % len(changes))


if __name__ == "__main__":
    try:
        main()
    except DiscordError as err:
        sys.exit("Discord API error: %s" % err)
