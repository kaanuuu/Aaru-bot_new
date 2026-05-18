"""
╔══════════════════════════════════════════════════════════════════╗
║                    🤖 AARU BOT v3.0 - ULTRA                    ║
║         Kill System | Fun | Admin | Games | Leaderboard        ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os, sys, json, time, random, asyncio, logging, sqlite3, datetime
from functools import wraps
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode, ChatAction
from telegram.error import TelegramError, BadRequest

try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    import anthropic as anthropic_lib
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


# ══════════════════════════════════════════════
#  CONFIG — Apni API keys yahan daalo
# ══════════════════════════════════════════════
class Config:
    BOT_TOKEN       = os.getenv("BOT_TOKEN",        "YOUR_BOT_TOKEN_HERE")
    AI_PROVIDER     = os.getenv("AI_PROVIDER",      "gemini")  # gemini | openai | anthropic | none
    GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY",   "")
    GEMINI_MODEL    = "gemini-2.0-flash"
    OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY",   "")
    OPENAI_MODEL    = "gpt-3.5-turbo"
    ANTHROPIC_KEY   = os.getenv("ANTHROPIC_API_KEY","YOUR_ANTHROPIC_KEY_HERE")
    ANTHROPIC_MODEL = "claude-3-haiku-20240307"

    BOT_NAME        = "Aaru"
    BOT_VERSION     = "3.0.0"
    OWNER_ID        = int(os.getenv("OWNER_ID", "8551681447"))
    ADMIN_IDS       = [int(x) for x in os.getenv("ADMIN_IDS","8551681447").split(",") if x.strip().isdigit()]
    DB_PATH         = os.getenv("DB_PATH", "aaru_v3.db")

    KILL_REWARD_MIN = 150
    KILL_REWARD_MAX = 200
    REVIVE_COST     = 500
    DAILY_REWARD    = 100
    HOURLY_REWARD   = 30
    RATE_LIMIT      = 8
    RATE_WINDOW     = 60
    AI_COOLDOWN     = 3

    AI_SYSTEM_PROMPT = """Tu Aaru hai — ek friendly, funny aur helpful AI.
Tu Hinglish (Hindi+English) mein baat karta hai.
Emojis use karta hai. 2-4 lines concise jawab. Hamesha positive! 😊"""


# ══════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    handlers=[logging.FileHandler("aaru.log"), logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("AaruBot")


# ══════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════
class Database:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()
        logger.info(f"DB ready: {path}")

    def _init(self):
        c = self.conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT DEFAULT '',
            full_name   TEXT DEFAULT '',
            coins       INTEGER DEFAULT 0,
            kills       INTEGER DEFAULT 0,
            deaths      INTEGER DEFAULT 0,
            is_dead     INTEGER DEFAULT 0,
            is_banned   INTEGER DEFAULT 0,
            warn_count  INTEGER DEFAULT 0,
            total_msgs  INTEGER DEFAULT 0,
            joined_at   TEXT DEFAULT (datetime('now')),
            last_seen   TEXT DEFAULT (datetime('now')),
            last_daily  TEXT DEFAULT NULL,
            last_hourly TEXT DEFAULT NULL,
            last_work   TEXT DEFAULT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS rpg_players (
            user_id   INTEGER PRIMARY KEY,
            hero_name TEXT DEFAULT 'Hero',
            hero_class TEXT DEFAULT 'warrior',
            level     INTEGER DEFAULT 1,
            xp        INTEGER DEFAULT 0,
            xp_needed INTEGER DEFAULT 100,
            hp        INTEGER DEFAULT 100,
            max_hp    INTEGER DEFAULT 100,
            mana      INTEGER DEFAULT 50,
            max_mana  INTEGER DEFAULT 50,
            attack    INTEGER DEFAULT 15,
            defense   INTEGER DEFAULT 8,
            gold      INTEGER DEFAULT 50,
            kills     INTEGER DEFAULT 0,
            deaths    INTEGER DEFAULT 0,
            inventory TEXT DEFAULT '[]'
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS chat_history (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, role TEXT, content TEXT,
            ts      TEXT DEFAULT (datetime('now'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS warn_log (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, by_admin INTEGER, reason TEXT,
            ts      TEXT DEFAULT (datetime('now'))
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS kill_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            killer_id INTEGER, victim_id INTEGER, coins INTEGER,
            ts        TEXT DEFAULT (datetime('now'))
        )""")
        self.conn.commit()

    def upsert(self, uid, username, full_name):
        self.conn.execute("""
            INSERT INTO users (user_id, username, full_name) VALUES (?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username, full_name=excluded.full_name,
                last_seen=datetime('now'), total_msgs=total_msgs+1
        """, (uid, username or "", full_name or ""))
        self.conn.commit()

    def get(self, uid):
        return self.conn.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()

    def update(self, uid, **kw):
        if not kw: return
        sets = ", ".join(f"{k}=?" for k in kw)
        self.conn.execute(f"UPDATE users SET {sets} WHERE user_id=?", [*kw.values(), uid])
        self.conn.commit()

    def all_users(self):
        return self.conn.execute("SELECT user_id, is_banned FROM users").fetchall()

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def is_banned(self, uid):
        r = self.conn.execute("SELECT is_banned FROM users WHERE user_id=?", (uid,)).fetchone()
        return bool(r and r["is_banned"])

    def is_dead(self, uid):
        r = self.conn.execute("SELECT is_dead FROM users WHERE user_id=?", (uid,)).fetchone()
        return bool(r and r["is_dead"])

    def log_kill(self, killer, victim, coins):
        self.conn.execute("INSERT INTO kill_log (killer_id,victim_id,coins) VALUES (?,?,?)", (killer, victim, coins))
        self.conn.commit()

    def top_coins(self, limit=10):
        return self.conn.execute(
            "SELECT user_id,username,full_name,coins FROM users WHERE is_banned=0 ORDER BY coins DESC LIMIT ?",
            (limit,)).fetchall()

    def top_kills(self, limit=10):
        return self.conn.execute(
            "SELECT user_id,username,full_name,kills,deaths FROM users WHERE is_banned=0 ORDER BY kills DESC LIMIT ?",
            (limit,)).fetchall()

    def add_warn(self, uid, by_admin, reason):
        self.conn.execute("INSERT INTO warn_log (user_id,by_admin,reason) VALUES (?,?,?)", (uid, by_admin, reason))
        self.conn.execute("UPDATE users SET warn_count=warn_count+1 WHERE user_id=?", (uid,))
        self.conn.commit()

    def clear_warns(self, uid):
        self.conn.execute("DELETE FROM warn_log WHERE user_id=?", (uid,))
        self.conn.execute("UPDATE users SET warn_count=0 WHERE user_id=?", (uid,))
        self.conn.commit()

    def add_msg(self, uid, role, content):
        self.conn.execute("INSERT INTO chat_history (user_id,role,content) VALUES (?,?,?)", (uid, role, content))
        self.conn.commit()

    def get_history(self, uid, limit=8):
        rows = self.conn.execute(
            "SELECT role,content FROM chat_history WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (uid, limit)).fetchall()
        return list(reversed(rows))

    def clear_history(self, uid):
        self.conn.execute("DELETE FROM chat_history WHERE user_id=?", (uid,))
        self.conn.commit()

    def get_rpg(self, uid):
        return self.conn.execute("SELECT * FROM rpg_players WHERE user_id=?", (uid,)).fetchone()

    def create_rpg(self, uid, name, cls, stats):
        self.conn.execute("""
            INSERT OR REPLACE INTO rpg_players
            (user_id,hero_name,hero_class,hp,max_hp,mana,max_mana,attack,defense)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (uid, name, cls, stats["hp"], stats["max_hp"], stats["mana"], stats["max_mana"], stats["attack"], stats["defense"]))
        self.conn.commit()

    def update_rpg(self, uid, **kw):
        if not kw: return
        sets = ", ".join(f"{k}=?" for k in kw)
        self.conn.execute(f"UPDATE rpg_players SET {sets} WHERE user_id=?", [*kw.values(), uid])
        self.conn.commit()

    def rpg_leaderboard(self, limit=10):
        return self.conn.execute("""
            SELECT r.hero_name,r.hero_class,r.level,r.kills,r.gold,u.username
            FROM rpg_players r JOIN users u ON r.user_id=u.user_id
            ORDER BY r.level DESC, r.kills DESC LIMIT ?
        """, (limit,)).fetchall()


# ══════════════════════════════════════════════
#  RPG DATA
# ══════════════════════════════════════════════
class RPGData:
    CLASSES = {
        "warrior": {"name":"⚔️ Warrior","emoji":"🛡️","hp":130,"max_hp":130,"mana":30,"max_mana":30,"attack":20,"defense":14},
        "mage":    {"name":"🔮 Mage",   "emoji":"🔮","hp":70, "max_hp":70, "mana":120,"max_mana":120,"attack":30,"defense":5},
        "rogue":   {"name":"🗡️ Rogue",  "emoji":"🗡️","hp":95, "max_hp":95, "mana":60, "max_mana":60, "attack":25,"defense":9},
        "archer":  {"name":"🏹 Archer", "emoji":"🏹","hp":85, "max_hp":85, "mana":45, "max_mana":45, "attack":22,"defense":7},
    }
    ENEMIES = [
        {"name":"🐺 Wolf",      "hp":45, "max_hp":45, "attack":10,"defense":3, "xp":25, "gold":12,"tier":1},
        {"name":"💀 Skeleton",  "hp":60, "max_hp":60, "attack":14,"defense":6, "xp":40, "gold":20,"tier":1},
        {"name":"🧟 Zombie",    "hp":80, "max_hp":80, "attack":16,"defense":8, "xp":50, "gold":28,"tier":2},
        {"name":"🧙 Dark Mage", "hp":65, "max_hp":65, "attack":22,"defense":4, "xp":60, "gold":35,"tier":2},
        {"name":"👹 Ogre",      "hp":110,"max_hp":110,"attack":24,"defense":10,"xp":90, "gold":55,"tier":3},
        {"name":"🐉 Dragon",    "hp":140,"max_hp":140,"attack":32,"defense":18,"xp":150,"gold":100,"tier":3},
        {"name":"👑 Demon King","hp":200,"max_hp":200,"attack":40,"defense":22,"xp":250,"gold":180,"tier":4},
    ]
    SHOP = {
        "health_potion": {"name":"🧪 Health Potion","cost":15,"type":"consumable","effect":{"hp":40},"desc":"Restore 40 HP"},
        "mana_potion":   {"name":"💙 Mana Potion",  "cost":18,"type":"consumable","effect":{"mana":50},"desc":"Restore 50 Mana"},
        "mega_potion":   {"name":"⚗️ Mega Potion",  "cost":40,"type":"consumable","effect":{"hp":100},"desc":"Restore 100 HP"},
        "iron_sword":    {"name":"⚔️ Iron Sword",   "cost":60,"type":"weapon",   "effect":{"attack":8},"desc":"+8 Attack"},
        "steel_sword":   {"name":"🗡️ Steel Sword",  "cost":120,"type":"weapon",  "effect":{"attack":18},"desc":"+18 Attack"},
        "magic_staff":   {"name":"🔱 Magic Staff",  "cost":80,"type":"weapon",   "effect":{"attack":14,"mana":20},"desc":"+14 ATK +20 Mana"},
        "iron_shield":   {"name":"🛡️ Iron Shield",  "cost":70,"type":"armor",    "effect":{"defense":10},"desc":"+10 Defense"},
        "amulet":        {"name":"📿 Lucky Amulet", "cost":90,"type":"accessory","effect":{"attack":5,"defense":5},"desc":"+5ATK +5DEF"},
    }


# ══════════════════════════════════════════════
#  MESSAGES
# ══════════════════════════════════════════════
KILL_MSGS = [
    "☠️ {killer} ne {victim} ko sniper se headshot maar diya! 💥",
    "🗡️ {killer} ne {victim} ka siir kaat diya! Khoon hi khoon! 🩸",
    "💣 {killer} ne {victim} ke neeche bomb blast kar diya! 💥",
    "⚡ {killer} ne {victim} par lightning strike kiya! Jal gaya! 🔥",
    "🔫 {killer} ne {victim} ko 360 noscope kiya! MLG style! 🎮",
    "🪓 {killer} ne {victim} ko axe se chop kar diya! Tukde tukde! 🪓",
    "🌊 {killer} ne {victim} ko dariya mein phenk diya! Glub glub! 🐟",
    "🚀 {killer} ne {victim} ko rocket launcher se udaa diya! Boom! 🚀",
    "💀 {killer} ne {victim} ko Uno reverse card de diya — vo maar gaya! 🃏",
    "🎯 {killer} ne {victim} par 47 knives phenke — sab lag gaye! 🎯",
    "👊 {killer} ne {victim} ko ek punch mein doosri duniya bhej diya!",
    "🌋 {killer} ne {victim} ke liye volcano summon kiya! Lava bath! 🌋",
    "☠️ {killer} ne {victim} ke juice mein poison daala... ched se piya! 🧃",
    "🎮 {killer} ne {victim} par aimbot lagaya aur khatam kar diya! GG",
    "🐍 {killer} ne {victim} ke juton mein saanp daal diya! 😱 DEAD!",
]

SLAP_MSGS = [
    "👋 {user1} ne {user2} ko itna zor se thapad maara ki chappal bhi nikal gayi! 👡",
    "🤚 {user1} ne {user2} ko flying slap diya! Sunn raha hai ab bhi! 😵",
    "✋ {user1} ne {user2} ke gaal pe haath rakha... zor se! 5 ungliyan print! 💢",
    "👐 {user1} ne {user2} ko ek baar mein TEEN thapad! Speed record! ⚡",
]
HUG_MSGS = [
    "🤗 {user1} ne {user2} ko pyaar se jhappi di! Itni tight ki saanp lene lage! 😄",
    "💕 {user1} aur {user2} ka hug dekh ke sab pighal gaye! 🫂",
    "🥰 {user1} ne {user2} ko bear hug diya! Dono khush! 🐻",
    "❤️ {user1} ne {user2} ko hug kiya! Aww! 🥺",
]
PUNCH_MSGS = [
    "👊 {user1} ne {user2} ko uppercut diya! Stars dekh raha! ⭐",
    "🥊 {user1} ne {user2} ko gut punch! Oof! 😣",
    "💥 {user1} ne {user2} ko kamehameha! KAAA-MEEE-HAAA! 🔥",
    "🤜 {user1} ne {user2} ko punch — {user2} ne 3 chakkar kaate! 🌀",
]
PAT_MSGS = [
    "🙌 {user1} ne {user2} ke sar par pyaar se haath rakkha! Good boy! 😊",
    "✨ {user1} ne {user2} ko sar par pat kiya — {user2} khush ho gaya! 🥰",
]
POKE_MSGS = [
    "👉 {user1} ne {user2} ko ghoor ghoor ke poke kiya! Hey! 😠",
    "☝️ {user1} ne {user2} ko finger se jabardasti poke kiya! Annoying! 😤",
]
STAB_MSGS = [
    "🗡️ {user1} ne {user2} ko chaku maar diya! Lekin {user2} buff nikla! 😤",
    "🔪 {user1} ne {user2} ko kitchen knife se stab kiya! Aar-par! 😱",
]

TRIVIA_QUESTIONS = [
    {"q":"India ki capital?",          "a":"delhi",                    "opts":["Mumbai","Delhi","Kolkata","Chennai"]},
    {"q":"Python kya hai?",            "a":"programming language",     "opts":["Programming Language","Snake","Movie","Game"]},
    {"q":"2 + 2 × 2 = ?",             "a":"6",                        "opts":["6","8","4","16"]},
    {"q":"Sabse bada ocean?",          "a":"pacific",                  "opts":["Pacific","Atlantic","Indian","Arctic"]},
    {"q":"Telegram kab launch hua?",   "a":"2013",                     "opts":["2013","2010","2015","2018"]},
    {"q":"HTML ka full form?",         "a":"hypertext markup language","opts":["HyperText Markup Language","High Tech Modern Lang","Hyper Transfer Markup","None"]},
    {"q":"Mount Everest kahan hai?",   "a":"nepal",                    "opts":["Nepal","India","China","Tibet"]},
    {"q":"CPU ka full form?",          "a":"central processing unit",  "opts":["Central Processing Unit","Computer Power Unit","Core Processor Unit","None"]},
    {"q":"Instagram kis company ka?",  "a":"meta",                     "opts":["Meta","Google","Twitter","Microsoft"]},
    {"q":"Sabse chhota planet?",       "a":"mercury",                  "opts":["Mercury","Mars","Venus","Pluto"]},
    {"q":"Water ka chemical formula?", "a":"h2o",                      "opts":["H2O","CO2","NaCl","O2"]},
    {"q":"Google kab bana?",           "a":"1998",                     "opts":["1998","2000","1995","2004"]},
]

RPS_WIN   = {"rock":"scissors","scissors":"paper","paper":"rock"}
RPS_EMOJI = {"rock":"🪨","scissors":"✂️","paper":"📄"}


# ══════════════════════════════════════════════
#  AI MANAGER
# ══════════════════════════════════════════════
class AIManager:
    def __init__(self):
        p = Config.AI_PROVIDER
        if p == "gemini" and GEMINI_AVAILABLE:
            genai.configure(api_key=Config.GEMINI_API_KEY)
            self.gmodel = genai.GenerativeModel(Config.GEMINI_MODEL)
            logger.info("AI: Gemini")
        elif p == "openai" and OPENAI_AVAILABLE:
            openai.api_key = Config.OPENAI_API_KEY
            logger.info("AI: OpenAI")
        elif p == "anthropic" and ANTHROPIC_AVAILABLE:
            self.aclient = anthropic_lib.Anthropic(api_key=Config.ANTHROPIC_KEY)
            logger.info("AI: Anthropic")
        else:
            logger.warning("No AI provider!")

    async def reply(self, uid, msg, history):
        try:
            p = Config.AI_PROVIDER
            if p == "gemini"    and GEMINI_AVAILABLE:    return await self._gemini(msg, history)
            if p == "openai"    and OPENAI_AVAILABLE:    return await self._openai(msg, history)
            if p == "anthropic" and ANTHROPIC_AVAILABLE: return await self._anthropic(msg, history)
            return "⚙️ AI configure nahi hua! .env mein API key daalo."
        except Exception as e:
            logger.error(f"AI error: {e}")
            return "⚠️ AI busy hai! Thodi der mein try karo. 😅"

    async def _gemini(self, msg, history):
        ch = [{"role":"user" if h["role"]=="user" else "model","parts":[h["content"]]} for h in history[-6:]]
        chat = self.gmodel.start_chat(history=ch)
        full = f"{Config.AI_SYSTEM_PROMPT}\n\n{msg}" if not ch else msg
        r = await asyncio.get_event_loop().run_in_executor(None, lambda: chat.send_message(full))
        return r.text

    async def _openai(self, msg, history):
        msgs = [{"role":"system","content":Config.AI_SYSTEM_PROMPT}]
        for h in history[-6:]:
            msgs.append({"role":h["role"],"content":h["content"]})
        msgs.append({"role":"user","content":msg})
        r = await asyncio.get_event_loop().run_in_executor(None,
            lambda: openai.chat.completions.create(model=Config.OPENAI_MODEL, messages=msgs, max_tokens=400))
        return r.choices[0].message.content

    async def _anthropic(self, msg, history):
        msgs = [{"role":h["role"],"content":h["content"]} for h in history[-6:]]
        msgs.append({"role":"user","content":msg})
        r = await asyncio.get_event_loop().run_in_executor(None,
            lambda: self.aclient.messages.create(model=Config.ANTHROPIC_MODEL, max_tokens=400,
                                                  system=Config.AI_SYSTEM_PROMPT, messages=msgs))
        return r.content[0].text


# ══════════════════════════════════════════════
#  RATE LIMITER
# ══════════════════════════════════════════════
class RateLimiter:
    def __init__(self):
        self.msgs = {}
        self.ai_cd = {}

    def ok(self, uid):
        now = time.time()
        self.msgs.setdefault(uid, [])
        self.msgs[uid] = [t for t in self.msgs[uid] if now - t < Config.RATE_WINDOW]
        if len(self.msgs[uid]) >= Config.RATE_LIMIT:
            return False
        self.msgs[uid].append(now)
        return True

    def ai_ok(self, uid):
        now = time.time()
        if uid in self.ai_cd and now - self.ai_cd[uid] < Config.AI_COOLDOWN:
            return False
        self.ai_cd[uid] = now
        return True


# ══════════════════════════════════════════════
#  BATTLE ENGINE
# ══════════════════════════════════════════════
class BattleEngine:
    def __init__(self, db):
        self.db = db
        self.battles = {}

    def start(self, uid, player):
        lvl = player["level"]
        eligible = [e for e in RPGData.ENEMIES if e["tier"] <= max(1, (lvl//3)+1)]
        e = dict(random.choice(eligible))
        scale = 1 + (lvl-1)*0.05
        e["hp"] = int(e["hp"]*scale); e["max_hp"] = e["hp"]; e["attack"] = int(e["attack"]*scale)
        self.battles[uid] = {"enemy":e,"p_hp":player["hp"],"p_max":player["max_hp"],
                              "p_mana":player["mana"],"p_max_mana":player["max_mana"],"turn":1}
        return self.battles[uid]

    def act(self, uid, action, player):
        b = self.battles.get(uid)
        if not b: return {"error":True}
        e = b["enemy"]; logs = []; result = "ongoing"

        if action == "attack":
            crit = random.random() < 0.15
            dmg = max(1, player["attack"]-e["defense"]+random.randint(-3,5))
            if crit: dmg = int(dmg*1.8); logs.append(f"💥 CRITICAL! {dmg} damage!")
            else: logs.append(f"⚔️ {dmg} damage to {e['name']}!")
            e["hp"] -= dmg

        elif action == "magic":
            if b["p_mana"] < 15: logs.append("❌ Mana nahi! Attack ya potion use karo.")
            else:
                dmg = int(player["attack"]*1.7+random.randint(5,15))
                b["p_mana"] -= 15; e["hp"] -= dmg
                logs.append(f"✨ Magic! {dmg} damage! (-15 Mana)")

        elif action == "potion":
            inv = json.loads(player["inventory"])
            pots = [i for i in inv if "potion" in i]
            if not pots: logs.append("❌ Koi potion nahi!")
            else:
                k = pots[0]; it = RPGData.SHOP.get(k,{}); inv.remove(k)
                eff = it.get("effect",{})
                if "hp"   in eff: b["p_hp"]   = min(b["p_max"],      b["p_hp"]+eff["hp"]);     logs.append(f"🧪 +{eff['hp']} HP!")
                if "mana" in eff: b["p_mana"] = min(b["p_max_mana"], b["p_mana"]+eff["mana"]); logs.append(f"💙 +{eff['mana']} Mana!")
                self.db.update_rpg(uid, inventory=json.dumps(inv))

        elif action == "flee":
            if random.random() < 0.6: logs.append("🏃 Bhaag gaye!"); result = "fled"
            else: logs.append("❌ Bhag nahi paaye!")

        if result == "ongoing" and e["hp"] > 0:
            ed = max(1, e["attack"]-player["defense"]+random.randint(-2,4))
            if random.random() < 0.1: logs.append("💨 Dodge!")
            else: b["p_hp"] -= ed; logs.append(f"💥 {e['name']}: {ed} damage!")

        if e["hp"] <= 0:
            result = "won"; xp,gld = e["xp"],e["gold"]
            logs.append(f"🎉 {e['name']} defeated! +{xp}XP +{gld}Gold")
            nx,ng,nk = player["xp"]+xp, player["gold"]+gld, player["kills"]+1
            upd = {"xp":nx,"gold":ng,"kills":nk,"hp":b["p_hp"],"mana":b["p_mana"]}
            if nx >= player["xp_needed"]:
                upd.update({"level":player["level"]+1,"xp":nx-player["xp_needed"],
                             "xp_needed":int(player["xp_needed"]*1.5),
                             "max_hp":player["max_hp"]+15,"hp":player["max_hp"]+15,
                             "attack":player["attack"]+3,"defense":player["defense"]+2})
                logs.append(f"⬆️ LEVEL UP! Level {player['level']+1}!")
            self.db.update_rpg(uid, **upd)
            del self.battles[uid]

        elif b["p_hp"] <= 0:
            result = "lost"; b["p_hp"] = 0
            logs.append("💀 Tum haare!")
            self.db.update_rpg(uid, hp=int(player["max_hp"]*0.3),
                               deaths=player["deaths"]+1, gold=max(0,player["gold"]-10))
            del self.battles[uid]

        elif result == "fled":
            self.db.update_rpg(uid, hp=b["p_hp"], mana=b["p_mana"])
            del self.battles[uid]
        else:
            b["turn"] += 1; b["enemy"] = e

        return {"result":result,"logs":logs,"battle":b if result=="ongoing" else None,
                "p_hp":b["p_hp"],"p_mana":b["p_mana"]}

    def status(self, b, player):
        e = b["enemy"]
        def bar(v,m,n=10): f=int((v/max(1,m))*n); return "█"*f+"░"*(n-f)
        return (f"━━━━ ⚔️ BATTLE ━━━━\n"
                f"👹 **{e['name']}**\n❤️ `{bar(e['hp'],e['max_hp'])}` {e['hp']}/{e['max_hp']}\n\n"
                f"🧑 **{player['hero_name']}** Lv.{player['level']}\n"
                f"❤️ `{bar(b['p_hp'],b['p_max'])}` {b['p_hp']}/{b['p_max']}\n"
                f"🔮 `{bar(b['p_mana'],b['p_max_mana'])}` {b['p_mana']}/{b['p_max_mana']}\n"
                f"Turn #{b['turn']}\n━━━━━━━━━━━━━━━━━━")


# ══════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════
class KB:
    @staticmethod
    def main():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Chat",      callback_data="m_chat"),
             InlineKeyboardButton("⚔️ RPG Game",  callback_data="m_rpg")],
            [InlineKeyboardButton("🎮 Games",     callback_data="m_games"),
             InlineKeyboardButton("💰 Economy",   callback_data="m_eco")],
            [InlineKeyboardButton("📊 Profile",   callback_data="m_profile"),
             InlineKeyboardButton("🏆 Top Lists", callback_data="m_top")],
            [InlineKeyboardButton("ℹ️ Help",      callback_data="m_help"),
             InlineKeyboardButton("⚙️ Settings",  callback_data="m_settings")],
        ])

    @staticmethod
    def rpg():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⚔️ Hunt",     callback_data="rpg_hunt"),
             InlineKeyboardButton("🏪 Shop",     callback_data="rpg_shop")],
            [InlineKeyboardButton("🎒 Inventory",callback_data="rpg_inv"),
             InlineKeyboardButton("📊 Stats",    callback_data="rpg_stats")],
            [InlineKeyboardButton("🏆 RPG Top",  callback_data="rpg_top"),
             InlineKeyboardButton("🔙 Menu",     callback_data="m_main")],
        ])

    @staticmethod
    def cls():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⚔️ Warrior",  callback_data="cls_warrior"),
             InlineKeyboardButton("🔮 Mage",     callback_data="cls_mage")],
            [InlineKeyboardButton("🗡️ Rogue",    callback_data="cls_rogue"),
             InlineKeyboardButton("🏹 Archer",   callback_data="cls_archer")],
        ])

    @staticmethod
    def battle():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⚔️ Attack",  callback_data="b_attack"),
             InlineKeyboardButton("✨ Magic",   callback_data="b_magic")],
            [InlineKeyboardButton("🧪 Potion",  callback_data="b_potion"),
             InlineKeyboardButton("🏃 Flee",    callback_data="b_flee")],
        ])

    @staticmethod
    def shop():
        rows = []; row = []
        for k, it in RPGData.SHOP.items():
            row.append(InlineKeyboardButton(f"{it['name']}({it['cost']}💰)", callback_data=f"buy_{k}"))
            if len(row)==2: rows.append(row); row = []
        if row: rows.append(row)
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="m_rpg")])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def games():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🎲 Dice",       callback_data="g_dice"),
             InlineKeyboardButton("🪨 RPS",         callback_data="g_rps")],
            [InlineKeyboardButton("🧠 Trivia",      callback_data="g_trivia"),
             InlineKeyboardButton("🎰 Slots",       callback_data="g_slots")],
            [InlineKeyboardButton("🔙 Menu",        callback_data="m_main")],
        ])

    @staticmethod
    def rps():
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("🪨 Rock",    callback_data="rps_rock"),
            InlineKeyboardButton("✂️ Scissors",callback_data="rps_scissors"),
            InlineKeyboardButton("📄 Paper",   callback_data="rps_paper"),
        ]])

    @staticmethod
    def top():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Top Coins",  callback_data="top_coins"),
             InlineKeyboardButton("💀 Top Kills",  callback_data="top_kills")],
            [InlineKeyboardButton("⭐ RPG Top",    callback_data="top_rpg"),
             InlineKeyboardButton("🔙 Menu",       callback_data="m_main")],
        ])

    @staticmethod
    def eco():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 Daily",   callback_data="eco_daily"),
             InlineKeyboardButton("⏰ Hourly",  callback_data="eco_hourly")],
            [InlineKeyboardButton("💼 Work",    callback_data="eco_work"),
             InlineKeyboardButton("💰 Balance", callback_data="eco_bal")],
            [InlineKeyboardButton("🔙 Menu",    callback_data="m_main")],
        ])

    @staticmethod
    def back(to="m_main"):
        return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=to)]])


# ══════════════════════════════════════════════
#  MAIN BOT
# ══════════════════════════════════════════════
class AaruBot:
    def __init__(self):
        self.db     = Database(Config.DB_PATH)
        self.ai     = AIManager()
        self.rl     = RateLimiter()
        self.battle = BattleEngine(self.db)
        self.trivia = {}

    # ── Decorators ────────────────────────────
    def reg(fn):
        @wraps(fn)
        async def w(self, u, c, *a, **kw):
            usr = u.effective_user
            if usr:
                self.db.upsert(usr.id, usr.username, usr.full_name)
                if self.db.is_banned(usr.id):
                    await u.effective_message.reply_text("🚫 Tum banned ho!"); return
            return await fn(self, u, c, *a, **kw)
        return w

    def throttle(fn):
        @wraps(fn)
        async def w(self, u, c, *a, **kw):
            if not self.rl.ok(u.effective_user.id):
                await u.effective_message.reply_text("⏱️ Thoda slow karo yaar!"); return
            return await fn(self, u, c, *a, **kw)
        return w

    def adm(fn):
        @wraps(fn)
        async def w(self, u, c, *a, **kw):
            uid = u.effective_user.id
            if uid != Config.OWNER_ID and uid not in Config.ADMIN_IDS:
                await u.effective_message.reply_text("❌ Sirf admins ke liye!"); return
            return await fn(self, u, c, *a, **kw)
        return w

    def _m(self, user):
        name = user.full_name or user.username or "User"
        return f"[{name}](tg://user?id={user.id})"

    def _fmt(self, s):
        h,m = divmod(int(s),3600); m,s = divmod(m,60)
        return (f"{h}h {m}m" if h else (f"{m}m {s}s" if m else f"{s}s"))

    # ══════════════════════════════════════════
    #  BASIC
    # ══════════════════════════════════════════
    @reg
    @throttle
    async def start(self, u, c):
        name = u.effective_user.first_name or "Dost"
        await u.message.reply_text(
            f"🌟 **Namaste {name}!** Main hoon **Aaru** 🤖\n\n"
            f"⚔️ Kill System | 🎮 RPG | 🎲 Games\n"
            f"💰 Economy | 👮 Admin | 💬 AI Chat\n\n"
            f"Neeche se choose karo! 👇",
            reply_markup=KB.main(), parse_mode=ParseMode.MARKDOWN
        )

    @reg
    async def help_cmd(self, u, c):
        await u.message.reply_text(
            "📖 **AARU BOT HELP**\n\n"
            "**🗡️ Kill System (Reply karke use karo):**\n"
            "`/kill` — Kisi ko kill karo (+150-200 coins)\n"
            "`/revive` — Khud ko revive karo (500 coins)\n"
            "`/kills` — Apni kill/death stats\n\n"
            "**😄 Fun (Reply karke):**\n"
            "`/slap` `/hug` `/punch` `/pat` `/poke` `/stab`\n\n"
            "**🏆 Top Lists:**\n"
            "`/top` — Top 10 coins\n"
            "`/topkills` — Top 10 killers\n"
            "`/toprpg` — Top RPG players\n\n"
            "**💰 Economy:**\n"
            "`/coins` — Balance\n"
            "`/daily` — 100+ coins (24h)\n"
            "`/hourly` — 30+ coins (1h)\n"
            "`/work` — 25-80 coins (30min)\n"
            "`/give` — Reply + amount se gift karo\n\n"
            "**🎮 Games:**\n"
            "`/dice` `/rps` `/trivia` `/slots`\n\n"
            "**⚔️ RPG:**\n"
            "`/rpg` `/hunt` `/shop` `/inventory` `/heal` `/setname`\n\n"
            "**👮 Admin:**\n"
            "`/warn` `/kick` `/mute` `/unmute` `/ban` `/unban`\n"
            "`/broadcast` `/botstats`\n\n"
            "**💬 AI:**\n"
            "`/chat <msg>` — AI se baat karo\n"
            "`/clear` — History clear\n"
            "`/profile` — Apna profile",
            parse_mode=ParseMode.MARKDOWN
        )

    # ══════════════════════════════════════════
    #  KILL SYSTEM
    # ══════════════════════════════════════════
    @reg
    @throttle
    async def kill(self, u, c):
        killer = u.effective_user
        if not u.message.reply_to_message:
            await u.message.reply_text(
                "❌ **Kisi ko reply karke /kill likho!**\n\n"
                "📌 Example:\n"
                "1. Kisi ke message par reply karo\n"
                "2. `/kill` type karo\n"
                "3. Bang! 💥", parse_mode=ParseMode.MARKDOWN); return

        victim = u.message.reply_to_message.from_user
        if victim.id == killer.id:
            await u.message.reply_text("😂 Khud ko nahi maar sakte! Therapy lo. 🛋️"); return
        if victim.is_bot:
            await u.message.reply_text("🤖 Bots ko nahi maar sakte! Unke paas soul nahi!"); return

        self.db.upsert(victim.id, victim.username, victim.full_name)

        if self.db.is_dead(killer.id):
            await u.message.reply_text(
                f"💀 Tum khud **dead** ho!\n"
                f"Pehle `/revive` karo ({Config.REVIVE_COST} coins chahiye).",
                parse_mode=ParseMode.MARKDOWN); return

        if self.db.is_dead(victim.id):
            await u.message.reply_text(
                f"☠️ {self._m(victim)} pehle se **DEAD** hai!\n"
                f"Zinda logon ko maaro. 😅",
                parse_mode=ParseMode.MARKDOWN); return

        coins = random.randint(Config.KILL_REWARD_MIN, Config.KILL_REWARD_MAX)
        kd = self.db.get(killer.id); vd = self.db.get(victim.id)
        self.db.update(killer.id, kills=kd["kills"]+1, coins=kd["coins"]+coins)
        self.db.update(victim.id, is_dead=1, deaths=vd["deaths"]+1)
        self.db.log_kill(killer.id, victim.id, coins)

        km = random.choice(KILL_MSGS).format(killer=self._m(killer), victim=self._m(victim))
        await u.message.reply_text(
            f"{km}\n\n"
            f"💰 **{self._m(killer)}** ko **{coins} coins** mile!\n"
            f"☠️ **{self._m(victim)}** ab **DEAD** hai!\n"
            f"💡 Victim `/revive` se wapas aa sakta hai ({Config.REVIVE_COST} coins).",
            parse_mode=ParseMode.MARKDOWN
        )

    @reg
    @throttle
    async def revive(self, u, c):
        uid = u.effective_user.id; user = self.db.get(uid)
        if not user or not user["is_dead"]:
            await u.message.reply_text("✅ Tum already zinda ho! 😄"); return
        if user["coins"] < Config.REVIVE_COST:
            await u.message.reply_text(
                f"💸 Revive ke liye **{Config.REVIVE_COST} coins** chahiye!\n"
                f"Tumhare paas: **{user['coins']} coins**\n\n"
                f"💡 `/daily`, `/hourly`, `/work` se coins kamao!",
                parse_mode=ParseMode.MARKDOWN); return
        self.db.update(uid, is_dead=0, coins=user["coins"]-Config.REVIVE_COST)
        await u.message.reply_text(
            f"✨ **REVIVED!** Tum wapas zinda ho! 🎉\n"
            f"💸 -{Config.REVIVE_COST} coins kaat liye.\n"
            f"💰 Remaining: **{user['coins']-Config.REVIVE_COST}**",
            parse_mode=ParseMode.MARKDOWN
        )

    @reg
    async def kills_cmd(self, u, c):
        uid = u.effective_user.id; user = self.db.get(uid)
        if not user: await u.message.reply_text("Pehle /start karo!"); return
        status = "☠️ DEAD — `/revive` karo" if user["is_dead"] else "✅ Zinda"
        await u.message.reply_text(
            f"⚔️ **Kill Stats**\n\n"
            f"💀 Kills: **{user['kills']}**\n"
            f"😵 Deaths: **{user['deaths']}**\n"
            f"💰 Coins: **{user['coins']}**\n"
            f"❤️ Status: {status}",
            parse_mode=ParseMode.MARKDOWN
        )

    # ══════════════════════════════════════════
    #  TOP LISTS
    # ══════════════════════════════════════════
    @reg
    async def top_coins(self, u, c):
        rows = self.db.top_coins(10); medals = ["🥇","🥈","🥉"]+["🔸"]*7
        txt = "💰 **TOP 10 COINS**\n━━━━━━━━━━━━━━━\n"
        for i,r in enumerate(rows):
            name = r["full_name"] or r["username"] or f"User{r['user_id']}"
            txt += f"{medals[i]} **{name}** — {r['coins']}💰\n"
        await u.message.reply_text(txt or "Koi nahi abhi!", parse_mode=ParseMode.MARKDOWN)

    @reg
    async def top_kills(self, u, c):
        rows = self.db.top_kills(10); medals = ["🥇","🥈","🥉"]+["🔸"]*7
        txt = "💀 **TOP 10 KILLERS**\n━━━━━━━━━━━━━━━\n"
        for i,r in enumerate(rows):
            name = r["full_name"] or r["username"] or f"User{r['user_id']}"
            txt += f"{medals[i]} **{name}** — {r['kills']}K / {r['deaths']}D\n"
        await u.message.reply_text(txt or "Koi nahi abhi!", parse_mode=ParseMode.MARKDOWN)

    @reg
    async def top_rpg(self, u, c):
        rows = self.db.rpg_leaderboard(10); medals = ["🥇","🥈","🥉"]+["🔸"]*7
        txt = "⭐ **TOP 10 RPG**\n━━━━━━━━━━━━━━━\n"
        for i,r in enumerate(rows):
            cls = RPGData.CLASSES.get(r["hero_class"],{})
            txt += f"{medals[i]} **{r['hero_name']}** {cls.get('emoji','')} Lv.{r['level']} | {r['kills']}💀 | {r['gold']}💰\n"
        await u.message.reply_text(txt or "Koi nahi abhi!", parse_mode=ParseMode.MARKDOWN)

    # ══════════════════════════════════════════
    #  ECONOMY
    # ══════════════════════════════════════════
    @reg
    async def coins_cmd(self, u, c):
        user = self.db.get(u.effective_user.id)
        if not user: await u.message.reply_text("Pehle /start karo!"); return
        status = "☠️ DEAD" if user["is_dead"] else "✅ Alive"
        await u.message.reply_text(
            f"💰 **Balance**\n\n{u.effective_user.full_name}\n"
            f"Coins: **{user['coins']}** 💰\nKills: **{user['kills']}** 💀\nStatus: {status}",
            parse_mode=ParseMode.MARKDOWN
        )

    @reg
    async def daily(self, u, c):
        uid = u.effective_user.id; user = self.db.get(uid); now = datetime.datetime.now()
        if user and user["last_daily"]:
            diff = (now - datetime.datetime.fromisoformat(user["last_daily"])).total_seconds()
            if diff < 86400:
                await u.message.reply_text(f"⏳ Next daily: **{self._fmt(86400-diff)}**", parse_mode=ParseMode.MARKDOWN); return
        reward = Config.DAILY_REWARD + random.randint(0,50)
        coins = (user["coins"] if user else 0) + reward
        self.db.update(uid, coins=coins, last_daily=now.isoformat())
        await u.message.reply_text(f"🎁 **Daily!** +**{reward} coins**\n💰 Total: **{coins}**", parse_mode=ParseMode.MARKDOWN)

    @reg
    async def hourly(self, u, c):
        uid = u.effective_user.id; user = self.db.get(uid); now = datetime.datetime.now()
        if user and user["last_hourly"]:
            diff = (now - datetime.datetime.fromisoformat(user["last_hourly"])).total_seconds()
            if diff < 3600:
                await u.message.reply_text(f"⏳ Next hourly: **{self._fmt(3600-diff)}**", parse_mode=ParseMode.MARKDOWN); return
        reward = Config.HOURLY_REWARD + random.randint(0,15)
        coins = (user["coins"] if user else 0) + reward
        self.db.update(uid, coins=coins, last_hourly=now.isoformat())
        await u.message.reply_text(f"⏰ **Hourly!** +**{reward} coins**\n💰 Total: **{coins}**", parse_mode=ParseMode.MARKDOWN)

    @reg
    async def work(self, u, c):
        uid = u.effective_user.id; user = self.db.get(uid); now = datetime.datetime.now()
        if user and user["last_work"]:
            diff = (now - datetime.datetime.fromisoformat(user["last_work"])).total_seconds()
            if diff < 1800:
                await u.message.reply_text(f"😴 Next work: **{self._fmt(1800-diff)}**", parse_mode=ParseMode.MARKDOWN); return
        jobs = [("👨‍💻 Coding kiya",40,80),("🚗 Pizza deliver kiya",30,60),
                ("🧹 Ghar saafa kiya",25,50),("📦 Parcel deliver kiya",35,70),
                ("🎵 Street mein gaana gaaya",20,45),("📚 Tuition padhaaya",40,75),
                ("🍔 Burger banaya",25,55),("📷 Photos kheenchi",30,65)]
        job,mn,mx = random.choice(jobs)
        reward = random.randint(mn,mx); coins = (user["coins"] if user else 0)+reward
        self.db.update(uid, coins=coins, last_work=now.isoformat())
        await u.message.reply_text(
            f"💼 **Work Done!**\n{job} → +**{reward} coins**\n💰 Total: **{coins}**\n_(Next: 30 min)_",
            parse_mode=ParseMode.MARKDOWN
        )

    @reg
    async def give_cmd(self, u, c):
        if not u.message.reply_to_message:
            await u.message.reply_text("❌ Reply karke `/give <amount>`", parse_mode=ParseMode.MARKDOWN); return
        if not c.args or not c.args[0].isdigit():
            await u.message.reply_text("❌ Use: reply karke `/give 100`", parse_mode=ParseMode.MARKDOWN); return
        amount = int(c.args[0]); giver = u.effective_user; recv = u.message.reply_to_message.from_user
        gd = self.db.get(giver.id)
        if recv.id == giver.id:            await u.message.reply_text("😂 Khud ko nahi de sakte!"); return
        if not gd or gd["coins"] < amount: await u.message.reply_text(f"❌ Itne coins nahi! Tumhare paas: {gd['coins'] if gd else 0}"); return
        if amount <= 0:                    await u.message.reply_text("❌ Amount > 0 hona chahiye!"); return
        self.db.upsert(recv.id, recv.username, recv.full_name)
        rd = self.db.get(recv.id)
        self.db.update(giver.id, coins=gd["coins"]-amount)
        self.db.update(recv.id,  coins=rd["coins"]+amount)
        await u.message.reply_text(
            f"💸 {self._m(giver)} ne {self._m(recv)} ko **{amount} coins** gift kiye! 🎁\n"
            f"Sender remaining: **{gd['coins']-amount}** 💰",
            parse_mode=ParseMode.MARKDOWN
        )

    # ══════════════════════════════════════════
    #  FUN COMMANDS
    # ══════════════════════════════════════════
    async def _fun(self, u, c, msgs, label):
        if not u.message.reply_to_message:
            await u.message.reply_text(f"❌ Kisi ko **reply** karke `/{label}` karo!", parse_mode=ParseMode.MARKDOWN); return
        txt = random.choice(msgs).format(user1=self._m(u.effective_user), user2=self._m(u.message.reply_to_message.from_user))
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    @reg
    @throttle
    async def slap(self,  u, c): await self._fun(u, c, SLAP_MSGS,  "slap")
    @reg
    @throttle
    async def hug(self,   u, c): await self._fun(u, c, HUG_MSGS,   "hug")
    @reg
    @throttle
    async def punch(self, u, c): await self._fun(u, c, PUNCH_MSGS, "punch")
    @reg
    @throttle
    async def pat(self,   u, c): await self._fun(u, c, PAT_MSGS,   "pat")
    @reg
    @throttle
    async def poke(self,  u, c): await self._fun(u, c, POKE_MSGS,  "poke")
    @reg
    @throttle
    async def stab(self,  u, c): await self._fun(u, c, STAB_MSGS,  "stab")

    # ══════════════════════════════════════════
    #  GAMES
    # ══════════════════════════════════════════
    @reg
    async def dice_cmd(self, u, c):
        d1,d2 = random.randint(1,6),random.randint(1,6); t = d1+d2
        res = "🎉 Double!" if d1==d2 else ("🔥 High!" if t>=10 else "💨 Low")
        await u.message.reply_text(f"🎲 **Dice Roll!**\n[ {d1} ] [ {d2} ] = **{t}** — {res}", parse_mode=ParseMode.MARKDOWN)

    @reg
    async def rps_cmd(self, u, c):
        await u.message.reply_text("🪨✂️📄 **RPS!** Choose:", reply_markup=KB.rps(), parse_mode=ParseMode.MARKDOWN)

    @reg
    async def trivia_cmd(self, u, c):
        uid = u.effective_user.id; q = random.choice(TRIVIA_QUESTIONS)
        self.trivia[uid] = q; opts = q["opts"][:]
        random.shuffle(opts)
        btns = [[InlineKeyboardButton(o, callback_data=f"trivia_{o.lower()}")] for o in opts]
        await u.message.reply_text(f"🧠 **TRIVIA!**\n\n❓ {q['q']}", reply_markup=InlineKeyboardMarkup(btns), parse_mode=ParseMode.MARKDOWN)

    @reg
    async def slots_cmd(self, u, c):
        uid = u.effective_user.id; user = self.db.get(uid); cost = 10
        if not user or user["coins"] < cost:
            await u.message.reply_text(f"❌ Slots ke liye {cost} coins chahiye!"); return
        syms = ["🍒","🍋","🍊","⭐","💎","7️⃣","🃏"]
        s1,s2,s3 = [random.choice(syms) for _ in range(3)]
        if s1==s2==s3: prize = (500 if s1=="💎" else (300 if s1=="7️⃣" else 50)); res = f"🎉 Three {s1}! +{prize} coins!"
        elif s1==s2 or s2==s3 or s1==s3: prize = 15; res = "✨ Pair! +15 coins!"
        else: prize = 0; res = "😔 No match!"
        nc = user["coins"]-cost+prize; self.db.update(uid, coins=nc)
        await u.message.reply_text(
            f"🎰 **SLOTS!**\n[ {s1} | {s2} | {s3} ]\n\n{res}\nBalance: **{nc}** 💰",
            parse_mode=ParseMode.MARKDOWN
        )

    # ══════════════════════════════════════════
    #  RPG
    # ══════════════════════════════════════════
    @reg
    async def rpg_cmd(self, u, c):
        uid = u.effective_user.id; rpg = self.db.get_rpg(uid)
        if not rpg:
            await u.message.reply_text("⚔️ **RPG**\n\nClass choose karo!", reply_markup=KB.cls(), parse_mode=ParseMode.MARKDOWN)
        else:
            cls = RPGData.CLASSES[rpg["hero_class"]]
            await u.message.reply_text(
                f"⚔️ **RPG MENU**\n{cls['emoji']} **{rpg['hero_name']}** Lv.{rpg['level']}\n❤️ {rpg['hp']}/{rpg['max_hp']}",
                reply_markup=KB.rpg(), parse_mode=ParseMode.MARKDOWN
            )

    @reg
    async def hunt_cmd(self, u, c):
        uid = u.effective_user.id
        if uid in self.battle.battles: await u.message.reply_text("⚔️ Pehle battle khatam karo!"); return
        rpg = self.db.get_rpg(uid)
        if not rpg: await u.message.reply_text("❌ Pehle /rpg se character banao!"); return
        b = self.battle.start(uid, rpg)
        await u.message.reply_text(self.battle.status(b, rpg), reply_markup=KB.battle(), parse_mode=ParseMode.MARKDOWN)

    @reg
    async def shop_cmd(self, u, c):
        rpg = self.db.get_rpg(u.effective_user.id); g = rpg["gold"] if rpg else 0
        txt = f"🏪 **SHOP** | 💰 {g} Gold\n\n"
        for k,it in RPGData.SHOP.items(): txt += f"{it['name']} — {it['cost']}💰 | {it['desc']}\n"
        await u.message.reply_text(txt, reply_markup=KB.shop(), parse_mode=ParseMode.MARKDOWN)

    @reg
    async def inventory_cmd(self, u, c):
        rpg = self.db.get_rpg(u.effective_user.id)
        if not rpg: await u.message.reply_text("❌ /rpg se character banao!"); return
        inv = json.loads(rpg["inventory"])
        if not inv: txt = "🎒 Bag khali!"
        else:
            txt = "🎒 **Inventory:**\n"; counts = {}
            for k in inv: counts[k] = counts.get(k,0)+1
            for k,ct in counts.items():
                it = RPGData.SHOP.get(k,{}); txt += f"{it.get('name',k)} ×{ct}\n"
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    @reg
    async def heal_cmd(self, u, c):
        uid = u.effective_user.id; rpg = self.db.get_rpg(uid)
        if not rpg: await u.message.reply_text("❌ /rpg se character banao!"); return
        inv = json.loads(rpg["inventory"]); pots = [i for i in inv if "potion" in i]
        if not pots: await u.message.reply_text("❌ Koi potion nahi! /shop se kharido."); return
        k = pots[0]; it = RPGData.SHOP.get(k,{}); eff = it.get("effect",{}); inv.remove(k)
        nh = min(rpg["max_hp"],rpg["hp"]+eff.get("hp",0)); nm = min(rpg["max_mana"],rpg["mana"]+eff.get("mana",0))
        self.db.update_rpg(uid, hp=nh, mana=nm, inventory=json.dumps(inv))
        await u.message.reply_text(f"🧪 **{it.get('name','Potion')}** used!\n❤️ {rpg['hp']}→{nh} | 🔮 {rpg['mana']}→{nm}", parse_mode=ParseMode.MARKDOWN)

    @reg
    async def setname_cmd(self, u, c):
        if not c.args: await u.message.reply_text("✍️ `/setname NaamYahan`", parse_mode=ParseMode.MARKDOWN); return
        name = " ".join(c.args)[:20]; rpg = self.db.get_rpg(u.effective_user.id)
        if rpg: self.db.update_rpg(u.effective_user.id, hero_name=name)
        else: c.user_data["pending_name"] = name
        await u.message.reply_text(f"✅ Hero naam: **{name}**", parse_mode=ParseMode.MARKDOWN)

    # ══════════════════════════════════════════
    #  ADMIN
    # ══════════════════════════════════════════
    @reg
    @adm
    async def warn_cmd(self, u, c):
        if not u.message.reply_to_message: await u.message.reply_text("❌ Reply karke /warn use karo!"); return
        victim = u.message.reply_to_message.from_user; reason = " ".join(c.args) if c.args else "No reason"
        self.db.upsert(victim.id, victim.username, victim.full_name)
        self.db.add_warn(victim.id, u.effective_user.id, reason)
        vd = self.db.get(victim.id)
        await u.message.reply_text(
            f"⚠️ {self._m(victim)} warned!\nReason: {reason}\nTotal: **{vd['warn_count']}/3**"
            f"{'  🚫 3 warns — ban consider karo!' if vd['warn_count']>=3 else ''}",
            parse_mode=ParseMode.MARKDOWN
        )

    @reg
    @adm
    async def kick_cmd(self, u, c):
        if not u.message.reply_to_message: await u.message.reply_text("❌ Reply karke /kick use karo!"); return
        victim = u.message.reply_to_message.from_user
        try:
            await c.bot.ban_chat_member(u.message.chat_id, victim.id)
            await c.bot.unban_chat_member(u.message.chat_id, victim.id)
            await u.message.reply_text(f"👢 {self._m(victim)} kick kiya!", parse_mode=ParseMode.MARKDOWN)
        except BadRequest as e: await u.message.reply_text(f"❌ Kick nahi hua: {e}")

    @reg
    @adm
    async def mute_cmd(self, u, c):
        if not u.message.reply_to_message: await u.message.reply_text("❌ Reply karke /mute [mins] use karo!"); return
        victim = u.message.reply_to_message.from_user
        mins = int(c.args[0]) if c.args and c.args[0].isdigit() else 10
        until = datetime.datetime.now() + datetime.timedelta(minutes=mins)
        try:
            await c.bot.restrict_chat_member(u.message.chat_id, victim.id,
                permissions=ChatPermissions(can_send_messages=False), until_date=until)
            await u.message.reply_text(f"🔇 {self._m(victim)} {mins}min mute!", parse_mode=ParseMode.MARKDOWN)
        except BadRequest as e: await u.message.reply_text(f"❌ Mute nahi hua: {e}")

    @reg
    @adm
    async def unmute_cmd(self, u, c):
        if not u.message.reply_to_message: await u.message.reply_text("❌ Reply karke /unmute use karo!"); return
        victim = u.message.reply_to_message.from_user
        try:
            await c.bot.restrict_chat_member(u.message.chat_id, victim.id,
                permissions=ChatPermissions(can_send_messages=True, can_send_media_messages=True,
                                            can_send_polls=True, can_send_other_messages=True))
            await u.message.reply_text(f"🔊 {self._m(victim)} unmuted!", parse_mode=ParseMode.MARKDOWN)
        except BadRequest as e: await u.message.reply_text(f"❌ Unmute nahi hua: {e}")

    @reg
    @adm
    async def ban_cmd(self, u, c):
        uid = None
        if u.message.reply_to_message: uid = u.message.reply_to_message.from_user.id
        elif c.args and c.args[0].isdigit(): uid = int(c.args[0])
        if not uid: await u.message.reply_text("❌ Reply ya /ban <id>"); return
        self.db.update(uid, is_banned=1)
        try: await c.bot.ban_chat_member(u.message.chat_id, uid)
        except: pass
        await u.message.reply_text(f"🚫 User `{uid}` banned!", parse_mode=ParseMode.MARKDOWN)

    @reg
    @adm
    async def unban_cmd(self, u, c):
        if not c.args or not c.args[0].isdigit(): await u.message.reply_text("❌ /unban <id>"); return
        uid = int(c.args[0]); self.db.update(uid, is_banned=0)
        try: await c.bot.unban_chat_member(u.message.chat_id, uid)
        except: pass
        await u.message.reply_text(f"✅ User `{uid}` unbanned!", parse_mode=ParseMode.MARKDOWN)

    @reg
    @adm
    async def broadcast_cmd(self, u, c):
        if not c.args: await u.message.reply_text("📣 /broadcast <message>"); return
        msg = " ".join(c.args); all_ = self.db.all_users(); s = f = 0
        await u.message.reply_text(f"📣 Broadcasting to {len(all_)} users...")
        for row in all_:
            if row["is_banned"]: continue
            try:
                await c.bot.send_message(row["user_id"], f"📢 **{Config.BOT_NAME}:**\n\n{msg}", parse_mode=ParseMode.MARKDOWN)
                s += 1; await asyncio.sleep(0.05)
            except: f += 1
        await u.message.reply_text(f"✅ Sent: {s} | ❌ Failed: {f}")

    @reg
    @adm
    async def botstats_cmd(self, u, c):
        uc  = self.db.count()
        rpc = self.db.conn.execute("SELECT COUNT(*) FROM rpg_players").fetchone()[0]
        mc  = self.db.conn.execute("SELECT COUNT(*) FROM chat_history").fetchone()[0]
        kc  = self.db.conn.execute("SELECT COUNT(*) FROM kill_log").fetchone()[0]
        await u.message.reply_text(
            f"📊 **BOT STATS**\n\n👥 Users: {uc}\n⚔️ RPG: {rpc}\n💬 Msgs: {mc}\n💀 Kills: {kc}\n🤖 AI: {Config.AI_PROVIDER}\nv{Config.BOT_VERSION}",
            parse_mode=ParseMode.MARKDOWN
        )

    # ══════════════════════════════════════════
    #  AI CHAT
    # ══════════════════════════════════════════
    @reg
    @throttle
    async def chat_cmd(self, u, c):
        if not c.args: await u.message.reply_text("💬 `/chat message`", parse_mode=ParseMode.MARKDOWN); return
        await self._ai(u, c, " ".join(c.args))

    @reg
    @throttle
    async def msg_handler(self, u, c):
        if not u.message or not u.message.text: return
        txt = u.message.text
        if txt.startswith("/"): return
        if u.message.chat.type != "private":
            bot_un = (await c.bot.get_me()).username
            if f"@{bot_un}" not in txt: return
            txt = txt.replace(f"@{bot_un}","").strip()
        await self._ai(u, c, txt)

    async def _ai(self, u, c, msg):
        uid = u.effective_user.id
        if not self.rl.ai_ok(uid): await u.message.reply_text("⏳ Ek second!"); return
        await c.bot.send_chat_action(u.effective_chat.id, ChatAction.TYPING)
        history = [{"role":r["role"],"content":r["content"]} for r in self.db.get_history(uid)]
        self.db.add_msg(uid, "user", msg)
        resp = await self.ai.reply(uid, msg, history)
        self.db.add_msg(uid, "assistant", resp)
        await u.message.reply_text(resp, parse_mode=ParseMode.MARKDOWN)

    @reg
    async def clear_cmd(self, u, c):
        self.db.clear_history(u.effective_user.id)
        await u.message.reply_text("🗑️ Chat history clear! ✨")

    @reg
    async def profile_cmd(self, u, c):
        uid = u.effective_user.id; user = self.db.get(uid); rpg = self.db.get_rpg(uid)
        status = "☠️ DEAD" if (user and user["is_dead"]) else "✅ Alive"
        txt = (f"👤 **{u.effective_user.full_name}**\n🆔 `{uid}`\n"
               f"💰 {user['coins'] if user else 0} | 💀 {user['kills'] if user else 0}K/{user['deaths'] if user else 0}D\n"
               f"❤️ {status}\n")
        if rpg: txt += f"\n⚔️ RPG: {rpg['hero_name']} Lv.{rpg['level']} {RPGData.CLASSES[rpg['hero_class']]['name']}"
        await u.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

    # ══════════════════════════════════════════
    #  CALLBACKS
    # ══════════════════════════════════════════
    async def cb(self, u, c):
        q = u.callback_query; await q.answer(); d = q.data; uid = u.effective_user.id

        if d == "m_main":
            await q.edit_message_text("🌟 Main Menu", reply_markup=KB.main())

        elif d == "m_chat":
            await q.edit_message_text("💬 **Chat Mode**\n\nSidha message ya `/chat <msg>`\n`/clear` se history clear",
                                       reply_markup=KB.back(), parse_mode=ParseMode.MARKDOWN)

        elif d == "m_games":
            await q.edit_message_text("🎮 **GAMES**", reply_markup=KB.games())

        elif d == "m_eco":
            user = self.db.get(uid)
            await q.edit_message_text(f"💰 **Economy**\nBalance: **{user['coins'] if user else 0} coins**",
                                       reply_markup=KB.eco(), parse_mode=ParseMode.MARKDOWN)

        elif d == "m_profile":
            user = self.db.get(uid); rpg = self.db.get_rpg(uid)
            status = "☠️ DEAD" if (user and user["is_dead"]) else "✅ Alive"
            txt = (f"👤 **{u.effective_user.full_name}**\n"
                   f"💰 {user['coins'] if user else 0} | 💀 {user['kills'] if user else 0} kills\n"
                   f"❤️ {status}\n")
            if rpg: txt += f"⭐ RPG Lv.{rpg['level']}"
            await q.edit_message_text(txt, reply_markup=KB.back(), parse_mode=ParseMode.MARKDOWN)

        elif d == "m_top":
            await q.edit_message_text("🏆 **TOP LISTS**", reply_markup=KB.top())

        elif d == "m_help":
            await q.edit_message_text("ℹ️ Full help: `/help`", reply_markup=KB.back(), parse_mode=ParseMode.MARKDOWN)

        elif d == "m_settings":
            await q.edit_message_text(f"⚙️ AI: {Config.AI_PROVIDER}\n`/setname naam` — Hero naam\n`/clear` — Chat clear",
                                       reply_markup=KB.back(), parse_mode=ParseMode.MARKDOWN)

        elif d == "m_rpg":
            rpg = self.db.get_rpg(uid)
            if not rpg:
                await q.edit_message_text("⚔️ Class choose karo:", reply_markup=KB.cls())
            else:
                cls = RPGData.CLASSES[rpg["hero_class"]]
                await q.edit_message_text(
                    f"⚔️ {cls['emoji']} **{rpg['hero_name']}** Lv.{rpg['level']}\n❤️ {rpg['hp']}/{rpg['max_hp']}",
                    reply_markup=KB.rpg(), parse_mode=ParseMode.MARKDOWN
                )

        elif d.startswith("cls_"):
            key = d[4:]; cls = RPGData.CLASSES.get(key)
            if not cls: return
            name = c.user_data.get("pending_name", u.effective_user.first_name)
            self.db.create_rpg(uid, name, key, cls)
            await q.edit_message_text(
                f"✅ **{cls['name']}** chosen!\n{cls['emoji']} {name}\n❤️{cls['max_hp']} ⚔️{cls['attack']} 🛡️{cls['defense']}\n\n/hunt se ladai shuru!",
                reply_markup=KB.rpg(), parse_mode=ParseMode.MARKDOWN
            )

        elif d == "rpg_hunt":
            rpg = self.db.get_rpg(uid)
            if not rpg:           await q.answer("Pehle class choose karo!"); return
            if uid in self.battle.battles: await q.answer("Battle chal raha hai!"); return
            b = self.battle.start(uid, rpg)
            await q.edit_message_text(self.battle.status(b, rpg), reply_markup=KB.battle(), parse_mode=ParseMode.MARKDOWN)

        elif d == "rpg_shop":
            rpg = self.db.get_rpg(uid); g = rpg["gold"] if rpg else 0
            await q.edit_message_text(f"🏪 **SHOP** | 💰 {g} Gold", reply_markup=KB.shop(), parse_mode=ParseMode.MARKDOWN)

        elif d == "rpg_inv":
            rpg = self.db.get_rpg(uid)
            if not rpg: await q.answer("RPG nahi hai!"); return
            inv = json.loads(rpg["inventory"])
            if not inv: txt = "🎒 Bag khali!"
            else:
                txt = "🎒 **Inventory:**\n"; counts = {}
                for k in inv: counts[k] = counts.get(k,0)+1
                for k,ct in counts.items():
                    it = RPGData.SHOP.get(k,{}); txt += f"{it.get('name',k)} ×{ct}\n"
            await q.edit_message_text(txt, reply_markup=KB.back("m_rpg"), parse_mode=ParseMode.MARKDOWN)

        elif d == "rpg_stats":
            rpg = self.db.get_rpg(uid)
            if not rpg: await q.answer("RPG nahi hai!"); return
            cls = RPGData.CLASSES[rpg["hero_class"]]
            await q.edit_message_text(
                f"📊 **{rpg['hero_name']}** {cls['emoji']}\nLv.{rpg['level']} | XP:{rpg['xp']}/{rpg['xp_needed']}\n"
                f"❤️{rpg['hp']}/{rpg['max_hp']} 🔮{rpg['mana']}/{rpg['max_mana']}\n"
                f"⚔️{rpg['attack']} 🛡️{rpg['defense']} 💰{rpg['gold']} 💀{rpg['kills']}",
                reply_markup=KB.back("m_rpg"), parse_mode=ParseMode.MARKDOWN
            )

        elif d == "rpg_top":
            rows = self.db.rpg_leaderboard(5); medals = ["🥇","🥈","🥉","🔸","🔸"]
            txt = "⭐ **RPG TOP**\n"
            for i,r in enumerate(rows): txt += f"{medals[i]} {r['hero_name']} Lv.{r['level']} | {r['kills']}💀\n"
            await q.edit_message_text(txt or "Koi nahi!", reply_markup=KB.back("m_rpg"), parse_mode=ParseMode.MARKDOWN)

        elif d.startswith("b_"):
            action = d[2:]; rpg = self.db.get_rpg(uid)
            if uid not in self.battle.battles:
                await q.edit_message_text("⚠️ Koi battle nahi! /hunt karo.", reply_markup=KB.rpg()); return
            res = self.battle.act(uid, action, rpg); logs_txt = "\n".join(res["logs"])
            if res["result"]   == "won":
                await q.edit_message_text(f"🎉 **JEET!**\n\n{logs_txt}", reply_markup=KB.rpg(), parse_mode=ParseMode.MARKDOWN)
            elif res["result"] == "lost":
                await q.edit_message_text(f"💀 **HAAR!**\n\n{logs_txt}", reply_markup=KB.rpg(), parse_mode=ParseMode.MARKDOWN)
            elif res["result"] in ("fled","error"):
                await q.edit_message_text(f"🏃 {logs_txt}", reply_markup=KB.rpg(), parse_mode=ParseMode.MARKDOWN)
            else:
                rpg2 = self.db.get_rpg(uid)
                await q.edit_message_text(
                    f"{self.battle.status(res['battle'],rpg2)}\n\n📜 {logs_txt}",
                    reply_markup=KB.battle(), parse_mode=ParseMode.MARKDOWN
                )

        elif d.startswith("buy_"):
            key = d[4:]; it = RPGData.SHOP.get(key); rpg = self.db.get_rpg(uid)
            if not it or not rpg: await q.answer("Item ya RPG nahi!"); return
            if rpg["gold"] < it["cost"]: await q.answer(f"Gold kam! {it['cost']} chahiye."); return
            upd = {"gold": rpg["gold"]-it["cost"]}
            if it["type"] == "consumable":
                inv = json.loads(rpg["inventory"]); inv.append(key); upd["inventory"] = json.dumps(inv)
            else:
                eff = it["effect"]
                if "attack"  in eff: upd["attack"]   = rpg["attack"]  +eff["attack"]
                if "defense" in eff: upd["defense"]  = rpg["defense"] +eff["defense"]
                if "mana"    in eff: upd["max_mana"] = rpg["max_mana"]+eff["mana"]
            self.db.update_rpg(uid, **upd); await q.answer(f"✅ {it['name']} liya!")
            rpg2 = self.db.get_rpg(uid)
            await q.edit_message_text(f"🏪 **SHOP** | 💰 {rpg2['gold']} Gold\n✅ {it['name']} mila!",
                                       reply_markup=KB.shop(), parse_mode=ParseMode.MARKDOWN)

        elif d == "top_coins":
            rows = self.db.top_coins(10); medals = ["🥇","🥈","🥉"]+["🔸"]*7
            txt = "💰 **TOP COINS**\n"
            for i,r in enumerate(rows):
                name = r["full_name"] or r["username"] or f"User{r['user_id']}"
                txt += f"{medals[i]} {name} — {r['coins']}💰\n"
            await q.edit_message_text(txt or "Koi nahi!", reply_markup=KB.top(), parse_mode=ParseMode.MARKDOWN)

        elif d == "top_kills":
            rows = self.db.top_kills(10); medals = ["🥇","🥈","🥉"]+["🔸"]*7
            txt = "💀 **TOP KILLS**\n"
            for i,r in enumerate(rows):
                name = r["full_name"] or r["username"] or f"User{r['user_id']}"
                txt += f"{medals[i]} {name} — {r['kills']}K/{r['deaths']}D\n"
            await q.edit_message_text(txt or "Koi nahi!", reply_markup=KB.top(), parse_mode=ParseMode.MARKDOWN)

        elif d == "top_rpg":
            rows = self.db.rpg_leaderboard(10); medals = ["🥇","🥈","🥉"]+["🔸"]*7
            txt = "⭐ **RPG TOP**\n"
            for i,r in enumerate(rows): txt += f"{medals[i]} {r['hero_name']} Lv.{r['level']} | {r['kills']}💀 | {r['gold']}💰\n"
            await q.edit_message_text(txt or "Koi nahi!", reply_markup=KB.top(), parse_mode=ParseMode.MARKDOWN)

        elif d in ("eco_daily","eco_hourly","eco_work","eco_bal"):
            user = self.db.get(uid); now = datetime.datetime.now()
            if d == "eco_daily":
                if user and user["last_daily"]:
                    diff = (now-datetime.datetime.fromisoformat(user["last_daily"])).total_seconds()
                    if diff < 86400: await q.answer(f"Next daily: {self._fmt(86400-diff)}", show_alert=True); return
                reward = Config.DAILY_REWARD+random.randint(0,50)
                coins = (user["coins"] if user else 0)+reward
                self.db.update(uid, coins=coins, last_daily=now.isoformat())
                await q.answer(f"🎁 +{reward} coins! Total: {coins}", show_alert=True)
            elif d == "eco_hourly":
                if user and user["last_hourly"]:
                    diff = (now-datetime.datetime.fromisoformat(user["last_hourly"])).total_seconds()
                    if diff < 3600: await q.answer(f"Next hourly: {self._fmt(3600-diff)}", show_alert=True); return
                reward = Config.HOURLY_REWARD+random.randint(0,15)
                coins = (user["coins"] if user else 0)+reward
                self.db.update(uid, coins=coins, last_hourly=now.isoformat())
                await q.answer(f"⏰ +{reward} coins! Total: {coins}", show_alert=True)
            elif d == "eco_work":
                if user and user["last_work"]:
                    diff = (now-datetime.datetime.fromisoformat(user["last_work"])).total_seconds()
                    if diff < 1800: await q.answer(f"Next work: {self._fmt(1800-diff)}", show_alert=True); return
                jobs = [("Coding",40,80),("Delivery",30,60),("Cooking",25,55)]
                job,mn,mx = random.choice(jobs); reward = random.randint(mn,mx)
                coins = (user["coins"] if user else 0)+reward
                self.db.update(uid, coins=coins, last_work=now.isoformat())
                await q.answer(f"💼 {job}: +{reward} coins!", show_alert=True)
            elif d == "eco_bal":
                await q.answer(f"💰 {user['coins'] if user else 0} coins", show_alert=True)

        elif d == "g_dice":
            d1,d2 = random.randint(1,6),random.randint(1,6); t=d1+d2
            res = "🎉 Double!" if d1==d2 else ("🔥 High!" if t>=10 else "💨 Low")
            await q.edit_message_text(f"🎲 **Dice!**\n[ {d1} ] [ {d2} ] = **{t}** — {res}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎲 Roll Again",callback_data="g_dice"),
                                                    InlineKeyboardButton("🔙",callback_data="m_games")]]),
                parse_mode=ParseMode.MARKDOWN)

        elif d == "g_rps":
            await q.edit_message_text("🪨✂️📄 **RPS!** Choose:", reply_markup=KB.rps(), parse_mode=ParseMode.MARKDOWN)

        elif d.startswith("rps_"):
            ch = d[4:]; bc = random.choice(["rock","scissors","paper"])
            if ch==bc: res="🤝 Tie!"
            elif RPS_WIN[ch]==bc: res="🎉 Tum jeete!"
            else: res="😔 Bot jeeta!"
            await q.edit_message_text(f"{RPS_EMOJI[ch]} vs {RPS_EMOJI[bc]}\n\n**{res}**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Again",callback_data="g_rps"),
                                                    InlineKeyboardButton("🔙",callback_data="m_games")]]),
                parse_mode=ParseMode.MARKDOWN)

        elif d == "g_trivia":
            q2 = random.choice(TRIVIA_QUESTIONS); self.trivia[uid] = q2
            opts = q2["opts"][:]; random.shuffle(opts)
            btns = [[InlineKeyboardButton(o, callback_data=f"trivia_{o.lower()}")] for o in opts]
            btns.append([InlineKeyboardButton("🔙",callback_data="m_games")])
            await q.edit_message_text(f"🧠 **TRIVIA!**\n\n❓ {q2['q']}", reply_markup=InlineKeyboardMarkup(btns), parse_mode=ParseMode.MARKDOWN)

        elif d.startswith("trivia_"):
            ans = d[7:]; qd = self.trivia.get(uid)
            if not qd: await q.answer("Question expire! Naya lo."); return
            if ans == qd["a"].lower():
                user = self.db.get(uid)
                if user: self.db.update(uid, coins=user["coins"]+20)
                await q.edit_message_text(f"✅ **Sahi!** +20 coins!\nAnswer: **{qd['a']}**",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧠 Next",callback_data="g_trivia"),
                                                        InlineKeyboardButton("🔙",callback_data="m_games")]]),
                    parse_mode=ParseMode.MARKDOWN)
            else:
                await q.edit_message_text(f"❌ **Galat!**\nSahi: **{qd['a']}**",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧠 Try Again",callback_data="g_trivia"),
                                                        InlineKeyboardButton("🔙",callback_data="m_games")]]),
                    parse_mode=ParseMode.MARKDOWN)
            del self.trivia[uid]

        elif d == "g_slots":
            user = self.db.get(uid); cost = 10
            if not user or user["coins"] < cost: await q.answer(f"❌ {cost} coins chahiye!"); return
            syms = ["🍒","🍋","🍊","⭐","💎","7️⃣","🃏"]
            s1,s2,s3 = [random.choice(syms) for _ in range(3)]
            if s1==s2==s3: prize=(500 if s1=="💎" else (300 if s1=="7️⃣" else 50)); res=f"🎉 Three {s1}! +{prize}"
            elif s1==s2 or s2==s3 or s1==s3: prize=15; res="✨ Pair! +15"
            else: prize=0; res="😔 No match"
            nc=user["coins"]-cost+prize; self.db.update(uid, coins=nc)
            await q.edit_message_text(f"🎰 [ {s1} | {s2} | {s3} ]\n{res}\nBalance: **{nc}** 💰",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎰 Spin",callback_data="g_slots"),
                                                    InlineKeyboardButton("🔙",callback_data="m_games")]]),
                parse_mode=ParseMode.MARKDOWN)

    async def err(self, u, c):
        logger.error(f"Error: {c.error}", exc_info=c.error)

    # ══════════════════════════════════════════
    #  RUN
    # ══════════════════════════════════════════
    def run(self):
        if Config.BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
            print("\n" + "="*50 + "\n❌ BOT_TOKEN set nahi hai!\n" + "="*50 + "\n")
            sys.exit(1)

        logger.info(f"🚀 {Config.BOT_NAME} v{Config.BOT_VERSION} starting...")
        app = Application.builder().token(Config.BOT_TOKEN).build()

        handlers = [
            ("start",       self.start),
            ("help",        self.help_cmd),
            ("kill",        self.kill),
            ("revive",      self.revive),
            ("kills",       self.kills_cmd),
            ("top",         self.top_coins),
            ("topkills",    self.top_kills),
            ("toprpg",      self.top_rpg),
            ("coins",       self.coins_cmd),
            ("daily",       self.daily),
            ("hourly",      self.hourly),
            ("work",        self.work),
            ("give",        self.give_cmd),
            ("slap",        self.slap),
            ("hug",         self.hug),
            ("punch",       self.punch),
            ("pat",         self.pat),
            ("poke",        self.poke),
            ("stab",        self.stab),
            ("dice",        self.dice_cmd),
            ("rps",         self.rps_cmd),
            ("trivia",      self.trivia_cmd),
            ("slots",       self.slots_cmd),
            ("rpg",         self.rpg_cmd),
            ("hunt",        self.hunt_cmd),
            ("shop",        self.shop_cmd),
            ("inventory",   self.inventory_cmd),
            ("heal",        self.heal_cmd),
            ("setname",     self.setname_cmd),
            ("chat",        self.chat_cmd),
            ("clear",       self.clear_cmd),
            ("profile",     self.profile_cmd),
            ("warn",        self.warn_cmd),
            ("kick",        self.kick_cmd),
            ("mute",        self.mute_cmd),
            ("unmute",      self.unmute_cmd),
            ("ban",         self.ban_cmd),
            ("unban",       self.unban_cmd),
            ("broadcast",   self.broadcast_cmd),
            ("botstats",    self.botstats_cmd),
        ]
        for name, handler in handlers:
            app.add_handler(CommandHandler(name, handler))
        app.add_handler(CallbackQueryHandler(self.cb))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.msg_handler))
        app.add_error_handler(self.err)

        logger.info(f"✅ {Config.BOT_NAME} LIVE! 🤖 ({len(handlers)} commands)")
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    AaruBot().run()
