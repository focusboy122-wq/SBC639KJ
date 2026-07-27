"""
SBC639KJBOT -- Fantasy Premier League (FPL) companion bot.

Free, ad-safe sports utility bot: deadline reminders, fixture difficulty,
today's price changes, captain-pick data, differentials, injury news, and
mini-league standings for Fantasy Premier League managers.

No betting, no odds, no "predictions" involving real money -- FPL itself is
the official free-to-play Premier League fantasy game, and everything this
bot shows (form, fixture difficulty, ownership, injury flags) is public,
factual FPL data, not a wagering or tipping service. That keeps it clear of
Telegram's restricted gambling/betting ad category.

Data source: the official public Fantasy Premier League API
(fantasy.premierleague.com/api) -- free, unauthenticated, no key needed.
"""

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DB_PATH = os.environ.get("DB_PATH", "sbc639.db")
FPL_BASE = "https://fantasy.premierleague.com/api"
TIMEOUT = 10

# ---------------------------------------------------------------------------
# Database (SQLite) -- stores each chat's linked FPL team ID
# ---------------------------------------------------------------------------

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, fpl_id INTEGER)"
        )
        conn.commit()


def set_fpl_id(chat_id: int, fpl_id: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users (chat_id, fpl_id) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET fpl_id = excluded.fpl_id",
            (chat_id, fpl_id),
        )
        conn.commit()


def get_fpl_id(chat_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT fpl_id FROM users WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return row["fpl_id"] if row else None


# ---------------------------------------------------------------------------
# FPL API helpers
# ---------------------------------------------------------------------------

_bootstrap_cache = {"data": None, "fetched_at": None}
CACHE_TTL_SECONDS = 20 * 60


def get_bootstrap():
    now = datetime.now(timezone.utc)
    cached = _bootstrap_cache["data"]
    fetched_at = _bootstrap_cache["fetched_at"]
    if cached and fetched_at and (now - fetched_at).total_seconds() < CACHE_TTL_SECONDS:
        return cached
    try:
        resp = requests.get(f"{FPL_BASE}/bootstrap-static/", timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        _bootstrap_cache["data"] = data
        _bootstrap_cache["fetched_at"] = now
        return data
    except requests.RequestException:
        return cached  # fall back to stale cache if FPL is briefly down


def get_current_and_next_event(bootstrap):
    events = bootstrap.get("events", [])
    current = next((e for e in events if e.get("is_current")), None)
    nxt = next((e for e in events if e.get("is_next")), None)
    return current, nxt


def format_deadline(deadline_iso: str):
    deadline = datetime.fromisoformat(deadline_iso.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    delta = deadline - now
    if delta.total_seconds() <= 0:
        return "Deadline has passed."
    days, rem = divmod(int(delta.total_seconds()), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def get_team_short_names(bootstrap):
    return {t["id"]: t["short_name"] for t in bootstrap.get("teams", [])}


def get_fixtures(event_id: int):
    try:
        resp = requests.get(f"{FPL_BASE}/fixtures/", params={"event": event_id}, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return []


def get_entry(fpl_id: int):
    try:
        resp = requests.get(f"{FPL_BASE}/entry/{fpl_id}/", timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return None


def get_picks(fpl_id: int, event_id: int):
    try:
        resp = requests.get(f"{FPL_BASE}/entry/{fpl_id}/event/{event_id}/picks/", timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return None


def get_league_standings(league_id: int):
    try:
        resp = requests.get(f"{FPL_BASE}/leagues-classic/{league_id}/standings/", timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to SBC639KJBOT \u2014 your Fantasy Premier League companion.\n\n"
        "Link your FPL team to get personalized data:\n"
        "/setup <your FPL team ID>\n\n"
        "Your team ID is the number in your FPL team URL, e.g.\n"
        "fantasy.premierleague.com/entry/1234567/event/1 \u2192 ID is 1234567\n\n"
        "Then try /deadline, /myteam, /captain, /fixtures, /help"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/setup <id> \u2014 link your FPL team\n"
        "/deadline \u2014 next gameweek deadline\n"
        "/myteam \u2014 your current squad & points\n"
        "/fixtures <team short name> \u2014 next 5 fixture difficulty (e.g. /fixtures ARS)\n"
        "/prices \u2014 today's actual price changes\n"
        "/captain \u2014 form + fixture data for your squad, to help you pick\n"
        "/differentials \u2014 low-ownership, in-form players\n"
        "/injuries \u2014 injury/news flags for your squad\n"
        "/minileague <id> \u2014 mini-league standings\n"
        "/about \u2014 about this bot"
    )


async def about_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "SBC639KJBOT is a free companion for Fantasy Premier League managers. "
        "It shows public FPL data \u2014 fixtures, form, price changes, injury news \u2014 "
        "to help you make your own transfer and captain calls. No betting, no "
        "odds, no real-money stakes."
    )


async def setup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: /setup <FPL team ID>\n"
            "Find it in your team URL: fantasy.premierleague.com/entry/<ID>/event/1"
        )
        return
    fpl_id = int(context.args[0])
    entry = get_entry(fpl_id)
    if not entry:
        await update.message.reply_text("Couldn't find that team ID. Double-check it and try again.")
        return
    set_fpl_id(update.effective_chat.id, fpl_id)
    name = f"{entry.get('player_first_name', '')} {entry.get('player_last_name', '')}".strip()
    await update.message.reply_text(f"Linked! Managing: {entry.get('name')} ({name})")


async def deadline_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bootstrap = get_bootstrap()
    if not bootstrap:
        await update.message.reply_text("FPL data is temporarily unavailable, try again shortly.")
        return
    _, nxt = get_current_and_next_event(bootstrap)
    if not nxt:
        await update.message.reply_text("No upcoming gameweek found (season may be over).")
        return
    remaining = format_deadline(nxt["deadline_time"])
    await update.message.reply_text(
        f"Gameweek {nxt['id']} deadline: {nxt['deadline_time'][:16].replace('T', ' ')} UTC\n"
        f"Time remaining: {remaining}"
    )


async def myteam_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    fpl_id = get_fpl_id(chat_id)
    if not fpl_id:
        await update.message.reply_text("Link your team first with /setup <FPL team ID>.")
        return

    bootstrap = get_bootstrap()
    current, _ = get_current_and_next_event(bootstrap)
    if not current:
        await update.message.reply_text("No gameweek is currently active.")
        return

    picks_data = get_picks(fpl_id, current["id"])
    entry = get_entry(fpl_id)
    if not picks_data or not entry:
        await update.message.reply_text("Couldn't load your team right now. Try again shortly.")
        return

    players_by_id = {p["id"]: p for p in bootstrap["elements"]}
    lines = []
    for pick in picks_data["picks"]:
        player = players_by_id.get(pick["element"])
        if not player:
            continue
        tag = " (C)" if pick["is_captain"] else " (VC)" if pick["is_vice_captain"] else ""
        lines.append(f"{player['web_name']}{tag}")

    total_points = picks_data.get("entry_history", {}).get("points")
    overall_rank = entry.get("summary_overall_rank")
    rank_str = f"{overall_rank:,}" if overall_rank else "N/A"
    await update.message.reply_text(
        f"{entry.get('name')} \u2014 GW{current['id']}: {total_points} pts\n"
        f"Overall rank: {rank_str}\n\n" +
        "\n".join(lines)
    )


async def fixtures_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /fixtures <team short name>\nExample: /fixtures ARS")
        return

    bootstrap = get_bootstrap()
    short_names = get_team_short_names(bootstrap)
    id_by_short = {v.upper(): k for k, v in short_names.items()}
    query = context.args[0].upper()
    team_id = id_by_short.get(query)
    if not team_id:
        options = ", ".join(sorted(short_names.values()))
        await update.message.reply_text(f"Unknown team \u201c{query}\u201d. Options: {options}")
        return

    _, nxt = get_current_and_next_event(bootstrap)
    if not nxt:
        await update.message.reply_text("No upcoming fixtures found.")
        return

    lines = []
    for gw in range(nxt["id"], nxt["id"] + 5):
        for fx in get_fixtures(gw):
            if fx["team_h"] == team_id:
                opp = short_names.get(fx["team_a"], "?")
                diff = fx["team_h_difficulty"]
                lines.append(f"GW{gw}: vs {opp} (H) \u2014 difficulty {diff}/5")
            elif fx["team_a"] == team_id:
                opp = short_names.get(fx["team_h"], "?")
                diff = fx["team_a_difficulty"]
                lines.append(f"GW{gw}: vs {opp} (A) \u2014 difficulty {diff}/5")

    if not lines:
        await update.message.reply_text("No fixtures found for that team in the next 5 gameweeks.")
    else:
        await update.message.reply_text(f"{short_names[team_id]} next fixtures:\n" + "\n".join(lines))


async def prices_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bootstrap = get_bootstrap()
    movers = [p for p in bootstrap["elements"] if p.get("cost_change_event", 0) != 0]
    if not movers:
        await update.message.reply_text("No price changes yet today.")
        return

    risers = sorted(
        [p for p in movers if p["cost_change_event"] > 0],
        key=lambda p: p["cost_change_event"], reverse=True,
    )[:5]
    fallers = sorted(
        [p for p in movers if p["cost_change_event"] < 0],
        key=lambda p: p["cost_change_event"],
    )[:5]

    lines = ["\U0001F4C8 Risers:"]
    lines += [f"  {p['web_name']} +\u00a3{p['cost_change_event']/10:.1f}m" for p in risers] or ["  None"]
    lines.append("\U0001F4C9 Fallers:")
    lines += [f"  {p['web_name']} \u00a3{p['cost_change_event']/10:.1f}m" for p in fallers] or ["  None"]
    await update.message.reply_text("Today's price changes:\n" + "\n".join(lines))


async def captain_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    fpl_id = get_fpl_id(chat_id)
    if not fpl_id:
        await update.message.reply_text("Link your team first with /setup <FPL team ID>.")
        return

    bootstrap = get_bootstrap()
    current, _ = get_current_and_next_event(bootstrap)
    if not current:
        await update.message.reply_text("No active gameweek right now.")
        return

    picks_data = get_picks(fpl_id, current["id"])
    if not picks_data:
        await update.message.reply_text("Couldn't load your squad. Try again shortly.")
        return

    players_by_id = {p["id"]: p for p in bootstrap["elements"]}
    squad = [players_by_id[p["element"]] for p in picks_data["picks"] if p["element"] in players_by_id]
    ranked = sorted(squad, key=lambda p: float(p.get("form") or 0), reverse=True)[:3]

    lines = [f"{p['web_name']} \u2014 form {p['form']}, {p['selected_by_percent']}% owned" for p in ranked]
    await update.message.reply_text(
        "Top form players in your squad (highest form first \u2014 use this alongside "
        "fixtures with /fixtures, not as a guarantee):\n" + "\n".join(lines)
    )


async def differentials_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bootstrap = get_bootstrap()
    candidates = [
        p for p in bootstrap["elements"]
        if float(p.get("selected_by_percent") or 100) < 5 and float(p.get("form") or 0) >= 4
    ]
    ranked = sorted(candidates, key=lambda p: float(p["form"]), reverse=True)[:5]
    if not ranked:
        await update.message.reply_text("No strong differentials found right now.")
        return
    lines = [f"{p['web_name']} \u2014 form {p['form']}, {p['selected_by_percent']}% owned" for p in ranked]
    await update.message.reply_text("Differentials worth a look:\n" + "\n".join(lines))


async def injuries_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    fpl_id = get_fpl_id(chat_id)
    bootstrap = get_bootstrap()
    players_by_id = {p["id"]: p for p in bootstrap["elements"]}

    if fpl_id:
        current, _ = get_current_and_next_event(bootstrap)
        picks_data = get_picks(fpl_id, current["id"]) if current else None
        pool = (
            [players_by_id[p["element"]] for p in picks_data["picks"] if p["element"] in players_by_id]
            if picks_data else []
        )
    else:
        pool = bootstrap["elements"]

    flagged = [p for p in pool if p.get("news")]
    if not flagged:
        await update.message.reply_text("No injury/news flags right now.")
        return
    lines = [f"{p['web_name']}: {p['news']}" for p in flagged[:10]]
    await update.message.reply_text("Injury/news watch:\n" + "\n".join(lines))


async def minileague_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /minileague <league ID>")
        return
    data = get_league_standings(int(context.args[0]))
    if not data:
        await update.message.reply_text("Couldn't load that mini-league. Check the ID and try again.")
        return
    results = data.get("standings", {}).get("results", [])[:10]
    if not results:
        await update.message.reply_text("No standings found for that league.")
        return
    lines = [f"{r['rank']}. {r['entry_name']} \u2014 {r['total']} pts" for r in results]
    await update.message.reply_text(f"{data['league']['name']}:\n" + "\n".join(lines))


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is not set.")

    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("about", about_cmd))
    app.add_handler(CommandHandler("setup", setup_cmd))
    app.add_handler(CommandHandler("deadline", deadline_cmd))
    app.add_handler(CommandHandler("myteam", myteam_cmd))
    app.add_handler(CommandHandler("fixtures", fixtures_cmd))
    app.add_handler(CommandHandler("prices", prices_cmd))
    app.add_handler(CommandHandler("captain", captain_cmd))
    app.add_handler(CommandHandler("differentials", differentials_cmd))
    app.add_handler(CommandHandler("injuries", injuries_cmd))
    app.add_handler(CommandHandler("minileague", minileague_cmd))

    logger.info("SBC639KJBOT starting (long polling)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
