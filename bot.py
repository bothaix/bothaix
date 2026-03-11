import csv
import os
import shutil
import time
import sqlite3
import threading
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# =========================
# CONFIG
# =========================
TOKEN = "8032117582:AAHWDHItwwrM51UZul-gYOUTzp6xgKk-EjE"
ADMIN_ID = 8586790829

MAIN_CHANNEL_USERNAME = "@haixdz"
MAIN_CHANNEL_LINK = "https://t.me/haixdz"

BACKUP_CHANNEL_USERNAME = "@haixdz2"
BACKUP_CHANNEL_LINK = "https://t.me/haixdz2"

POINTS_PER_REFERRAL = 10
SYNC_INTERVAL_SECONDS = 300

DB_FILE = "data.db"
BACKUP_DIR = "db_backups"

if not TOKEN or TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
    raise ValueError("حط التوكن الصحيح في TOKEN")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# =========================
# DATABASE
# =========================
db = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = db.cursor()
db_lock = threading.Lock()

def ensure_backup_dir():
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR, exist_ok=True)

def auto_backup_db():
    ensure_backup_dir()
    if os.path.exists(DB_FILE):
        backup_name = os.path.join(BACKUP_DIR, f"startup_backup_{int(time.time())}.db")
        try:
            shutil.copy2(DB_FILE, backup_name)
            print(f"Auto backup created: {backup_name}")
        except Exception as e:
            print(f"Auto backup failed: {e}")

def table_exists(table_name):
    with db_lock:
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name=?
        """, (table_name,))
        return cursor.fetchone() is not None

def get_columns(table_name):
    with db_lock:
        cursor.execute(f"PRAGMA table_info({table_name})")
        return [row[1] for row in cursor.fetchall()]

def ensure_column(table_name, column_name, column_def):
    cols = get_columns(table_name)
    if column_name not in cols:
        with db_lock:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")
            db.commit()
        print(f"Added column {column_name} to {table_name}")

def migrate_db():
    auto_backup_db()

    with db_lock:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            referrer INTEGER,
            points INTEGER DEFAULT 0,
            joined_at INTEGER
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            invited_user INTEGER PRIMARY KEY,
            referrer INTEGER NOT NULL,
            counted INTEGER DEFAULT 0,
            created_at INTEGER,
            counted_at INTEGER
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS rewards_shop (
            reward_id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            cost INTEGER NOT NULL,
            active INTEGER DEFAULT 1
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS redemptions (
            redeem_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            reward_id INTEGER NOT NULL,
            cost INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at INTEGER,
            processed_at INTEGER
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS blacklist (
            user_id INTEGER PRIMARY KEY,
            reason TEXT,
            added_at INTEGER
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS cooldowns (
            user_id INTEGER PRIMARY KEY,
            last_click INTEGER DEFAULT 0
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS gift_codes (
            code TEXT PRIMARY KEY,
            points INTEGER NOT NULL,
            max_uses INTEGER DEFAULT 0,
            uses_count INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at INTEGER
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS gift_code_claims (
            code TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            claimed_at INTEGER,
            PRIMARY KEY (code, user_id)
        )
        """)

        db.commit()

    if table_exists("users"):
        ensure_column("users", "username", "TEXT")
        ensure_column("users", "full_name", "TEXT")
        ensure_column("users", "referrer", "INTEGER")
        ensure_column("users", "points", "INTEGER DEFAULT 0")
        ensure_column("users", "joined_at", "INTEGER")

        cols = get_columns("users")
        if "invites" in cols:
            with db_lock:
                cursor.execute("""
                    UPDATE users
                    SET points = CASE
                        WHEN (points IS NULL OR points = 0) AND invites IS NOT NULL THEN invites * ?
                        ELSE points
                    END
                """, (POINTS_PER_REFERRAL,))
                db.commit()

migrate_db()

# =========================
# HELPERS
# =========================
def now():
    return int(time.time())

def fmt(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "-"

def is_admin(uid):
    return uid == ADMIN_ID

def anti_spam(user_id, seconds=2):
    current = now()
    with db_lock:
        cursor.execute("SELECT last_click FROM cooldowns WHERE user_id=?", (user_id,))
        row = cursor.fetchone()

        if row and current - row[0] < seconds:
            return False

        cursor.execute("""
            INSERT INTO cooldowns (user_id, last_click)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET last_click=excluded.last_click
        """, (user_id, current))
        db.commit()
    return True

def is_blacklisted(user_id):
    with db_lock:
        cursor.execute("SELECT 1 FROM blacklist WHERE user_id=?", (user_id,))
        return cursor.fetchone() is not None

def is_subscribed_to(channel_username, user_id):
    try:
        member = bot.get_chat_member(channel_username, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

def is_fully_subscribed(user_id):
    return (
        is_subscribed_to(MAIN_CHANNEL_USERNAME, user_id) and
        is_subscribed_to(BACKUP_CHANNEL_USERNAME, user_id)
    )

def get_points(user_id):
    with db_lock:
        cursor.execute("SELECT points FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
        return row[0] if row else 0

def add_points(user_id, amount):
    with db_lock:
        cursor.execute("""
            UPDATE users
            SET points = COALESCE(points, 0) + ?
            WHERE user_id=?
        """, (amount, user_id))
        db.commit()

def remove_points(user_id, amount):
    with db_lock:
        cursor.execute("""
            UPDATE users
            SET points = CASE
                WHEN COALESCE(points, 0) >= ? THEN COALESCE(points, 0) - ?
                ELSE 0
            END
            WHERE user_id=?
        """, (amount, amount, user_id))
        db.commit()

def user_exists(user_id):
    with db_lock:
        cursor.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
        return cursor.fetchone() is not None

def get_total_users():
    with db_lock:
        cursor.execute("SELECT COUNT(*) FROM users")
        return cursor.fetchone()[0]

def get_rank(user_id):
    with db_lock:
        cursor.execute("""
            SELECT user_id, COALESCE(points, 0)
            FROM users
            ORDER BY COALESCE(points, 0) DESC, joined_at ASC
        """)
        rows = cursor.fetchall()

    for i, (uid, _) in enumerate(rows, start=1):
        if uid == user_id:
            return i
    return None

def get_display_name(user_id, username, full_name):
    if full_name:
        if username:
            return f"{full_name} (@{username})"
        return full_name
    if username:
        return f"@{username}"
    return f"ID {user_id}"

# =========================
# USER / REFERRAL LOGIC
# =========================
def register_user(message, ref=None):
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip()

    # Anti-Cheat: منع دعوة النفس
    if ref == user_id:
        ref = None

    with db_lock:
        cursor.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
        exists = cursor.fetchone()

        if exists:
            cursor.execute("""
                UPDATE users
                SET username=?, full_name=?
                WHERE user_id=?
            """, (username, full_name, user_id))
            db.commit()
            return

        cursor.execute("""
            INSERT INTO users (user_id, username, full_name, referrer, points, joined_at)
            VALUES (?, ?, ?, ?, 0, ?)
        """, (user_id, username, full_name, ref, now()))

        # Anti-Cheat: يسجل الإحالة فقط إذا ref موجود
        if ref and ref != user_id:
            cursor.execute("SELECT 1 FROM users WHERE user_id=?", (ref,))
            if cursor.fetchone():
                cursor.execute("""
                    INSERT OR IGNORE INTO referrals (
                        invited_user, referrer, counted, created_at, counted_at
                    ) VALUES (?, ?, 0, ?, NULL)
                """, (user_id, ref, now()))

        db.commit()

def count_referral(invited_user):
    # Anti-Cheat: لازم يكون مشترك في القناتين
    if not is_fully_subscribed(invited_user):
        return False

    with db_lock:
        cursor.execute("""
            SELECT referrer, counted
            FROM referrals
            WHERE invited_user=?
        """, (invited_user,))
        row = cursor.fetchone()

        if not row:
            return False

        referrer, counted = row

        # Anti-Cheat: منع الاحتساب مرتين
        if counted == 1:
            return False

        cursor.execute("""
            UPDATE referrals
            SET counted=1, counted_at=?
            WHERE invited_user=?
        """, (now(), invited_user))
        db.commit()

    add_points(referrer, POINTS_PER_REFERRAL)

    try:
        bot.send_message(
            referrer,
            f"🎉 تم احتساب إحالة جديدة\n💰 +{POINTS_PER_REFERRAL} نقاط\n📊 رصيدك الآن: {get_points(referrer)}"
        )
    except:
        pass

    return True

def uncount_referral(invited_user):
    with db_lock:
        cursor.execute("""
            SELECT referrer, counted
            FROM referrals
            WHERE invited_user=?
        """, (invited_user,))
        row = cursor.fetchone()

        if not row:
            return False

        referrer, counted = row
        if counted != 1:
            return False

        cursor.execute("""
            UPDATE referrals
            SET counted=0, counted_at=NULL
            WHERE invited_user=?
        """, (invited_user,))
        db.commit()

    remove_points(referrer, POINTS_PER_REFERRAL)

    try:
        bot.send_message(
            referrer,
            f"⚠️ خرج واحد من الإحالات من إحدى القنوات\n💸 -{POINTS_PER_REFERRAL} نقاط\n📊 رصيدك الآن: {get_points(referrer)}"
        )
    except:
        pass

    return True

def sync_user_referral(invited_user):
    subscribed = is_fully_subscribed(invited_user)

    with db_lock:
        cursor.execute("SELECT counted FROM referrals WHERE invited_user=?", (invited_user,))
        row = cursor.fetchone()

    if not row:
        return False

    counted = row[0]

    if subscribed and counted == 0:
        return count_referral(invited_user)

    if (not subscribed) and counted == 1:
        return uncount_referral(invited_user)

    return False

def get_invited_users(referrer_id):
    with db_lock:
        cursor.execute("""
            SELECT u.user_id, u.username, u.full_name, r.counted, u.joined_at
            FROM referrals r
            LEFT JOIN users u ON r.invited_user = u.user_id
            WHERE r.referrer=?
            ORDER BY u.joined_at DESC
        """, (referrer_id,))
        return cursor.fetchall()

# =========================
# GIFT CODES
# =========================
def create_gift_code(code, points, max_uses=0):
    code = code.upper().strip()
    with db_lock:
        cursor.execute("""
            INSERT INTO gift_codes (code, points, max_uses, uses_count, active, created_at)
            VALUES (?, ?, ?, 0, 1, ?)
        """, (code, points, max_uses, now()))
        db.commit()

def redeem_code(user_id, code):
    code = code.upper().strip()

    with db_lock:
        cursor.execute("""
            SELECT points, max_uses, uses_count, active
            FROM gift_codes
            WHERE code=?
        """, (code,))
        row = cursor.fetchone()

        if not row:
            return False, "❌ الكود غير موجود."

        points, max_uses, uses_count, active = row

        if active != 1:
            return False, "❌ هذا الكود غير مفعل."

        cursor.execute("""
            SELECT 1 FROM gift_code_claims
            WHERE code=? AND user_id=?
        """, (code, user_id))
        if cursor.fetchone():
            return False, "❌ لقد استعملت هذا الكود من قبل."

        if max_uses > 0 and uses_count >= max_uses:
            return False, "❌ هذا الكود انتهت استعمالاته."

        cursor.execute("""
            INSERT INTO gift_code_claims (code, user_id, claimed_at)
            VALUES (?, ?, ?)
        """, (code, user_id, now()))

        cursor.execute("""
            UPDATE gift_codes
            SET uses_count = uses_count + 1
            WHERE code=?
        """, (code,))
        db.commit()

    add_points(user_id, points)
    return True, f"✅ تم استبدال الكود بنجاح\n💰 +{points} نقطة"

# =========================
# SHOP / ORDERS
# =========================
def get_active_rewards():
    with db_lock:
        cursor.execute("""
            SELECT reward_id, title, cost
            FROM rewards_shop
            WHERE active=1
            ORDER BY cost ASC, reward_id ASC
        """)
        return cursor.fetchall()

def send_shop(user_id):
    rewards = get_active_rewards()
    points = get_points(user_id)

    if not rewards:
        bot.send_message(user_id, "🛍 المتجر فارغ حالياً.")
        return

    text = f"🛍 <b>متجر الاستبدال</b>\n\n💰 رصيدك: {points} نقطة\n\n"
    markup = InlineKeyboardMarkup()

    for reward_id, title, cost in rewards:
        text += f"#{reward_id} • {title} — {cost} نقطة\n"
        markup.row(
            InlineKeyboardButton(
                f"استبدال #{reward_id}",
                callback_data=f"redeem_{reward_id}"
            )
        )

    bot.send_message(user_id, text, reply_markup=markup)

def redeem_reward(user_id, reward_id):
    with db_lock:
        cursor.execute("""
            SELECT title, cost, active
            FROM rewards_shop
            WHERE reward_id=?
        """, (reward_id,))
        reward = cursor.fetchone()

    if not reward:
        return False, "❌ العنصر غير موجود."

    title, cost, active = reward

    if active != 1:
        return False, "❌ العنصر غير متاح."

    points = get_points(user_id)
    if points < cost:
        return False, f"❌ نقاطك غير كافية.\n💰 رصيدك: {points}\n💸 المطلوب: {cost}"

    remove_points(user_id, cost)

    with db_lock:
        cursor.execute("""
            INSERT INTO redemptions (user_id, reward_id, cost, status, created_at)
            VALUES (?, ?, ?, 'pending', ?)
        """, (user_id, reward_id, cost, now()))
        order_id = cursor.lastrowid
        db.commit()

    try:
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("✅ موافقة", callback_data=f"approve_order_{order_id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"reject_order_{order_id}")
        )
        bot.send_message(
            ADMIN_ID,
            f"🛒 <b>طلب استبدال جديد</b>\n"
            f"👤 المستخدم: <code>{user_id}</code>\n"
            f"🎁 العنصر: {title}\n"
            f"💸 التكلفة: {cost} نقطة",
            reply_markup=markup
        )
    except:
        pass

    return True, f"✅ تم إرسال طلب الاستبدال: {title}"

def send_orders(user_id):
    with db_lock:
        cursor.execute("""
            SELECT r.redeem_id, s.title, r.cost, r.status, r.created_at
            FROM redemptions r
            JOIN rewards_shop s ON r.reward_id = s.reward_id
            WHERE r.user_id=?
            ORDER BY r.created_at DESC
            LIMIT 20
        """, (user_id,))
        rows = cursor.fetchall()

    if not rows:
        bot.send_message(user_id, "📦 ماعندك حتى طلب.")
        return

    text = "📦 <b>طلباتك</b>\n\n"
    for redeem_id, title, cost, status, created_at in rows:
        text += (
            f"#{redeem_id}\n"
            f"🎁 {title}\n"
            f"💸 {cost} نقطة\n"
            f"📌 {status}\n"
            f"🕒 {fmt(created_at)}\n\n"
        )

    bot.send_message(user_id, text)

# =========================
# INFO / EXTRA
# =========================
def send_info(user_id):
    text = f"""ℹ️ <b>معلومات البوت</b>

1️⃣ خذ رابطك الخاص
2️⃣ كل شخص يدخل من رابطك ويشترك في:
• {MAIN_CHANNEL_USERNAME}
• {BACKUP_CHANNEL_USERNAME}

3️⃣ كل إحالة صحيحة = <b>{POINTS_PER_REFERRAL} نقاط</b>
4️⃣ استبدل نقاطك من المتجر

⚠️ القوانين:
- العضو لازم يبقى مشترك في القناتين
- إذا خرج من قناة، تنقص النقاط
- يمنع دعوة نفسك
- يمنع الغش
- أكواد الهدايا لكل مستخدم مرة واحدة فقط
"""
    bot.send_message(user_id, text)

def send_invited_list(user_id):
    rows = get_invited_users(user_id)

    if not rows:
        bot.send_message(user_id, "👥 ما دعوت حتى شخص لحد الآن.")
        return

    text = "👥 <b>الأشخاص الذين دعوتهم</b>\n\n"
    for i, (uid, username, full_name, counted, joined_at) in enumerate(rows[:30], start=1):
        status = "✅ محتسبة" if counted == 1 else "❌ غير محتسبة"
        name = get_display_name(uid, username, full_name)
        text += f"{i}. {name}\n🆔 <code>{uid}</code>\n📌 {status}\n🕒 {fmt(joined_at)}\n\n"

    bot.send_message(user_id, text)

# =========================
# KEYBOARDS
# =========================
def make_user_buttons():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📢 الرئيسية", url=MAIN_CHANNEL_LINK),
        InlineKeyboardButton("📢 الاحتياطية", url=BACKUP_CHANNEL_LINK)
    )
    markup.row(
        InlineKeyboardButton("✅ تحقق", callback_data="check_sub"),
        InlineKeyboardButton("💰 نقاطي", callback_data="my_points")
    )
    markup.row(
        InlineKeyboardButton("🛍 المتجر", callback_data="shop"),
        InlineKeyboardButton("🔗 رابطي", callback_data="my_link")
    )
    markup.row(
        InlineKeyboardButton("🏆 ترتيبي", callback_data="my_rank"),
        InlineKeyboardButton("📦 طلباتي", callback_data="my_orders")
    )
    markup.row(
        InlineKeyboardButton("👥 دعوتي", callback_data="my_invites"),
        InlineKeyboardButton("ℹ️ معلومات", callback_data="info")
    )
    return markup

def make_admin_panel():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats"),
        InlineKeyboardButton("🏆 أفضل 10", callback_data="admin_top")
    )
    markup.row(
        InlineKeyboardButton("📦 الطلبات", callback_data="admin_orders"),
        InlineKeyboardButton("🛍 المتجر", callback_data="admin_shop")
    )
    markup.row(
        InlineKeyboardButton("➕ إضافة نقاط", callback_data="admin_help_addpoints"),
        InlineKeyboardButton("➖ نزع نقاط", callback_data="admin_help_removepoints")
    )
    markup.row(
        InlineKeyboardButton("👤 بحث مستخدم", callback_data="admin_help_user"),
        InlineKeyboardButton("📢 إذاعة", callback_data="admin_help_broadcast")
    )
    markup.row(
        InlineKeyboardButton("🎁 أكواد الهدايا", callback_data="admin_help_codes"),
        InlineKeyboardButton("🚫 حظر", callback_data="admin_help_blacklist")
    )
    markup.row(
        InlineKeyboardButton("✅ فك الحظر", callback_data="admin_help_unblacklist"),
        InlineKeyboardButton("💾 Backup", callback_data="admin_backup")
    )
    markup.row(
        InlineKeyboardButton("📁 Export CSV", callback_data="admin_export")
    )
    return markup

def make_order_manage_buttons(order_id):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✅ موافقة", callback_data=f"approve_order_{order_id}"),
        InlineKeyboardButton("❌ رفض", callback_data=f"reject_order_{order_id}")
    )
    return markup

def make_reward_manage_buttons(reward_id, active):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("💲 تعديل السعر", callback_data=f"edit_reward_{reward_id}"),
        InlineKeyboardButton("🔄 تفعيل/تعطيل", callback_data=f"toggle_reward_{reward_id}")
    )
    return markup

# =========================
# MESSAGES
# =========================
def send_main(user_id):
    me = bot.get_me()
    link = f"https://t.me/{me.username}?start={user_id}"
    points = get_points(user_id)

    main_ok = "✅" if is_subscribed_to(MAIN_CHANNEL_USERNAME, user_id) else "❌"
    backup_ok = "✅" if is_subscribed_to(BACKUP_CHANNEL_USERNAME, user_id) else "❌"

    text = f"""👋 <b>مرحبا بك</b>

🔗 <b>رابطك الخاص:</b>
<code>{link}</code>

💰 <b>نقاطك:</b> {points}
🎯 <b>كل إحالة صحيحة = {POINTS_PER_REFERRAL} نقاط</b>

📢 <b>الاشتراك الإجباري:</b>
{main_ok} {MAIN_CHANNEL_USERNAME}
{backup_ok} {BACKUP_CHANNEL_USERNAME}
"""

    bot.send_message(user_id, text, reply_markup=make_user_buttons())

# =========================
# USER COMMANDS
# =========================
@bot.message_handler(commands=['start'])
def start_cmd(message):
    user_id = message.from_user.id

    if is_blacklisted(user_id):
        bot.send_message(user_id, "🚫 لا يمكنك استخدام هذا البوت.")
        return

    args = message.text.split()
    ref = int(args[1]) if len(args) > 1 and args[1].isdigit() else None

    register_user(message, ref)
    sync_user_referral(user_id)
    send_main(user_id)

@bot.message_handler(commands=['points'])
def points_cmd(message):
    bot.send_message(message.chat.id, f"💰 نقاطك: {get_points(message.from_user.id)}")

@bot.message_handler(commands=['rank'])
def rank_cmd(message):
    rank = get_rank(message.from_user.id)
    if rank is None:
        bot.send_message(message.chat.id, "❌ لا توجد بيانات.")
        return
    bot.send_message(
        message.chat.id,
        f"🏆 ترتيبك: {rank} من {get_total_users()}\n💰 نقاطك: {get_points(message.from_user.id)}"
    )

@bot.message_handler(commands=['shop'])
def shop_cmd(message):
    send_shop(message.from_user.id)

@bot.message_handler(commands=['orders'])
def orders_cmd(message):
    send_orders(message.from_user.id)

@bot.message_handler(commands=['myid'])
def myid_cmd(message):
    bot.send_message(message.chat.id, f"🆔 <code>{message.from_user.id}</code>")

@bot.message_handler(commands=['invited'])
def invited_cmd(message):
    send_invited_list(message.from_user.id)

@bot.message_handler(commands=['info'])
def info_cmd(message):
    send_info(message.from_user.id)

@bot.message_handler(commands=['redeem'])
def redeem_cmd(message):
    parts = message.text.split()
    if len(parts) != 2:
        bot.send_message(message.chat.id, "❌ الاستعمال:\n<code>/redeem CODE</code>")
        return
    ok, msg = redeem_code(message.from_user.id, parts[1])
    bot.send_message(message.chat.id, msg)

# =========================
# ADMIN COMMANDS
# =========================
@bot.message_handler(commands=['admin'])
def admin_cmd(message):
    if not is_admin(message.from_user.id):
        return

    text = """🛠 <b>لوحة الأدمن الاحترافية</b>

الأوامر:

/stats
/top
/user USER_ID
/addpoints USER_ID AMOUNT
/removepoints USER_ID AMOUNT
/addreward اسم الهدية | السعر
/shoplist
/delreward REWARD_ID
/togglereward REWARD_ID
/editreward REWARD_ID NEW_PRICE
/orderslist
/approve ORDER_ID
/reject ORDER_ID
/createcode CODE POINTS
/codeslist
/disablecode CODE
/broadcast نص الرسالة
/blacklist USER_ID السبب
/unblacklist USER_ID
/backup
/export
"""
    bot.send_message(ADMIN_ID, text, reply_markup=make_admin_panel())

@bot.message_handler(commands=['stats'])
def stats_cmd(message):
    if not is_admin(message.from_user.id):
        return

    with db_lock:
        cursor.execute("SELECT COUNT(*) FROM users")
        users_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM referrals WHERE counted=1")
        counted_refs = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM redemptions WHERE status='pending'")
        pending_orders = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM rewards_shop WHERE active=1")
        active_rewards = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM blacklist")
        blacklist_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM gift_codes WHERE active=1")
        active_codes = cursor.fetchone()[0]

    bot.send_message(
        ADMIN_ID,
        f"""📊 <b>إحصائيات البوت</b>

👥 المستخدمون: {users_count}
✅ الإحالات المحتسبة: {counted_refs}
📦 الطلبات المعلقة: {pending_orders}
🛍 عناصر المتجر المفعلة: {active_rewards}
🎁 الأكواد المفعلة: {active_codes}
🚫 المحظورون: {blacklist_count}
💎 كل إحالة = {POINTS_PER_REFERRAL} نقاط
"""
    )

@bot.message_handler(commands=['top'])
def top_cmd(message):
    if not is_admin(message.from_user.id):
        return

    with db_lock:
        cursor.execute("""
            SELECT user_id, username, full_name, points
            FROM users
            ORDER BY points DESC, joined_at ASC
            LIMIT 10
        """)
        rows = cursor.fetchall()

    if not rows:
        bot.send_message(ADMIN_ID, "📭 ماكانش بيانات")
        return

    text = "🏆 <b>أفضل 10 مستخدمين بالنقاط</b>\n\n"
    for i, (uid, username, full_name, points) in enumerate(rows, start=1):
        name = get_display_name(uid, username, full_name)
        text += f"{i}. {name}\n🆔 <code>{uid}</code> | 💰 {points}\n\n"

    bot.send_message(ADMIN_ID, text)

@bot.message_handler(commands=['user'])
def user_cmd(message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        bot.send_message(ADMIN_ID, "❌ /user USER_ID")
        return

    uid = int(args[1])
    sync_user_referral(uid)

    with db_lock:
        cursor.execute("""
            SELECT user_id, username, full_name, referrer, points, joined_at
            FROM users
            WHERE user_id=?
        """, (uid,))
        user = cursor.fetchone()

    if not user:
        bot.send_message(ADMIN_ID, "❌ المستخدم غير موجود")
        return

    user_id, username, full_name, referrer, points, joined_at = user

    bot.send_message(
        ADMIN_ID,
        f"""👤 <b>بيانات المستخدم</b>

🆔 <code>{user_id}</code>
👤 {full_name or '-'}
📛 @{username if username else '-'}
👥 Referrer: <code>{referrer}</code>
💰 Points: {points}
🏆 Rank: {get_rank(user_id)}
🕒 Joined: {fmt(joined_at)}
📌 الرئيسية: {"نعم" if is_subscribed_to(MAIN_CHANNEL_USERNAME, user_id) else "لا"}
📌 الاحتياطية: {"نعم" if is_subscribed_to(BACKUP_CHANNEL_USERNAME, user_id) else "لا"}
"""
    )

@bot.message_handler(commands=['addpoints'])
def addpoints_cmd(message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) != 3 or not args[1].isdigit() or not args[2].isdigit():
        bot.send_message(ADMIN_ID, "❌ الاستعمال:\n<code>/addpoints USER_ID AMOUNT</code>")
        return

    user_id = int(args[1])
    amount = int(args[2])

    with db_lock:
        cursor.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
        if not cursor.fetchone():
            bot.send_message(ADMIN_ID, "❌ المستخدم غير موجود")
            return

    add_points(user_id, amount)

    try:
        bot.send_message(user_id, f"🎁 تم إضافة {amount} نقطة\n💰 رصيدك الآن: {get_points(user_id)}")
    except:
        pass

    bot.send_message(ADMIN_ID, f"✅ تمت إضافة {amount} نقطة للمستخدم <code>{user_id}</code>")

@bot.message_handler(commands=['removepoints'])
def removepoints_cmd(message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) != 3 or not args[1].isdigit() or not args[2].isdigit():
        bot.send_message(ADMIN_ID, "❌ الاستعمال:\n<code>/removepoints USER_ID AMOUNT</code>")
        return

    user_id = int(args[1])
    amount = int(args[2])

    with db_lock:
        cursor.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
        if not cursor.fetchone():
            bot.send_message(ADMIN_ID, "❌ المستخدم غير موجود")
            return

    remove_points(user_id, amount)

    try:
        bot.send_message(user_id, f"⚠️ تم نزع {amount} نقطة\n💰 رصيدك الآن: {get_points(user_id)}")
    except:
        pass

    bot.send_message(ADMIN_ID, f"✅ تم نزع {amount} نقطة من المستخدم <code>{user_id}</code>")

@bot.message_handler(commands=['broadcast'])
def broadcast_cmd(message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        bot.send_message(ADMIN_ID, "❌ /broadcast نص الرسالة")
        return

    text = parts[1]

    with db_lock:
        cursor.execute("SELECT user_id FROM users")
        users = cursor.fetchall()

    sent = 0
    failed = 0

    for (uid,) in users:
        if is_blacklisted(uid):
            continue
        try:
            bot.send_message(uid, text)
            sent += 1
            time.sleep(0.03)
        except:
            failed += 1

    bot.send_message(ADMIN_ID, f"📢 تم الإرسال\n✅ نجح: {sent}\n❌ فشل: {failed}")

@bot.message_handler(commands=['blacklist'])
def blacklist_cmd(message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        bot.send_message(ADMIN_ID, "❌ /blacklist USER_ID السبب")
        return

    uid = int(parts[1])
    reason = parts[2] if len(parts) > 2 else "بدون سبب"

    with db_lock:
        cursor.execute("""
            INSERT INTO blacklist (user_id, reason, added_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET reason=excluded.reason, added_at=excluded.added_at
        """, (uid, reason, now()))
        db.commit()

    bot.send_message(ADMIN_ID, f"🚫 تم حظر <code>{uid}</code>\nالسبب: {reason}")

@bot.message_handler(commands=['unblacklist'])
def unblacklist_cmd(message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        bot.send_message(ADMIN_ID, "❌ /unblacklist USER_ID")
        return

    uid = int(args[1])

    with db_lock:
        cursor.execute("DELETE FROM blacklist WHERE user_id=?", (uid,))
        db.commit()

    bot.send_message(ADMIN_ID, f"✅ تم فك الحظر عن <code>{uid}</code>")

@bot.message_handler(commands=['addreward'])
def addreward_cmd(message):
    if not is_admin(message.from_user.id):
        return

    text = message.text.replace("/addreward", "", 1).strip()
    if "|" not in text:
        bot.send_message(ADMIN_ID, "❌ /addreward اسم الهدية | السعر")
        return

    title, cost = [x.strip() for x in text.split("|", 1)]
    if not cost.isdigit():
        bot.send_message(ADMIN_ID, "❌ السعر لازم يكون رقم")
        return

    with db_lock:
        cursor.execute("""
            INSERT INTO rewards_shop (title, cost, active)
            VALUES (?, ?, 1)
        """, (title, int(cost)))
        db.commit()

    bot.send_message(ADMIN_ID, f"✅ تمت إضافة {title} بسعر {cost}")

@bot.message_handler(commands=['shoplist'])
def shoplist_cmd(message):
    if not is_admin(message.from_user.id):
        return

    with db_lock:
        cursor.execute("""
            SELECT reward_id, title, cost, active
            FROM rewards_shop
            ORDER BY reward_id DESC
        """)
        rows = cursor.fetchall()

    if not rows:
        bot.send_message(ADMIN_ID, "📭 المتجر فارغ")
        return

    for reward_id, title, cost, active in rows:
        status = "✅ مفعّل" if active == 1 else "❌ متوقف"
        bot.send_message(
            ADMIN_ID,
            f"🛍 <b>عنصر #{reward_id}</b>\n🎁 {title}\n💸 {cost} نقطة\n📌 {status}",
            reply_markup=make_reward_manage_buttons(reward_id, active)
        )

@bot.message_handler(commands=['delreward'])
def delreward_cmd(message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        bot.send_message(ADMIN_ID, "❌ /delreward REWARD_ID")
        return

    reward_id = int(args[1])

    with db_lock:
        cursor.execute("DELETE FROM rewards_shop WHERE reward_id=?", (reward_id,))
        db.commit()

    bot.send_message(ADMIN_ID, f"✅ تم حذف العنصر #{reward_id}")

@bot.message_handler(commands=['togglereward'])
def togglereward_cmd(message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        bot.send_message(ADMIN_ID, "❌ /togglereward REWARD_ID")
        return

    reward_id = int(args[1])

    with db_lock:
        cursor.execute("SELECT active FROM rewards_shop WHERE reward_id=?", (reward_id,))
        row = cursor.fetchone()
        if not row:
            bot.send_message(ADMIN_ID, "❌ العنصر غير موجود")
            return

        new_active = 0 if row[0] == 1 else 1
        cursor.execute("UPDATE rewards_shop SET active=? WHERE reward_id=?", (new_active, reward_id))
        db.commit()

    bot.send_message(ADMIN_ID, f"✅ تم تحديث حالة العنصر #{reward_id}")

@bot.message_handler(commands=['editreward'])
def editreward_cmd(message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) != 3 or not args[1].isdigit() or not args[2].isdigit():
        bot.send_message(ADMIN_ID, "❌ /editreward REWARD_ID NEW_PRICE")
        return

    reward_id = int(args[1])
    new_price = int(args[2])

    with db_lock:
        cursor.execute("SELECT 1 FROM rewards_shop WHERE reward_id=?", (reward_id,))
        if not cursor.fetchone():
            bot.send_message(ADMIN_ID, "❌ العنصر غير موجود")
            return

        cursor.execute("UPDATE rewards_shop SET cost=? WHERE reward_id=?", (new_price, reward_id))
        db.commit()

    bot.send_message(ADMIN_ID, f"✅ تم تعديل سعر العنصر #{reward_id} إلى {new_price}")

@bot.message_handler(commands=['orderslist'])
def orderslist_cmd(message):
    if not is_admin(message.from_user.id):
        return

    with db_lock:
        cursor.execute("""
            SELECT r.redeem_id, r.user_id, s.title, r.cost, r.status, r.created_at
            FROM redemptions r
            JOIN rewards_shop s ON r.reward_id = s.reward_id
            ORDER BY r.created_at DESC
            LIMIT 30
        """)
        rows = cursor.fetchall()

    if not rows:
        bot.send_message(ADMIN_ID, "📭 لا توجد طلبات")
        return

    for redeem_id, user_id, title, cost, status, created_at in rows:
        bot.send_message(
            ADMIN_ID,
            f"📦 <b>طلب #{redeem_id}</b>\n👤 <code>{user_id}</code>\n🎁 {title}\n💸 {cost}\n📌 {status}\n🕒 {fmt(created_at)}",
            reply_markup=make_order_manage_buttons(redeem_id) if status == "pending" else None
        )

@bot.message_handler(commands=['approve'])
def approve_cmd(message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        bot.send_message(ADMIN_ID, "❌ /approve ORDER_ID")
        return

    approve_order(int(args[1]))

@bot.message_handler(commands=['reject'])
def reject_cmd(message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        bot.send_message(ADMIN_ID, "❌ /reject ORDER_ID")
        return

    reject_order(int(args[1]))

def approve_order(order_id):
    with db_lock:
        cursor.execute("SELECT user_id, status FROM redemptions WHERE redeem_id=?", (order_id,))
        row = cursor.fetchone()

        if not row:
            bot.send_message(ADMIN_ID, "❌ الطلب غير موجود")
            return

        user_id, status = row
        if status != "pending":
            bot.send_message(ADMIN_ID, "❌ هذا الطلب تمت معالجته من قبل")
            return

        cursor.execute("""
            UPDATE redemptions
            SET status='approved', processed_at=?
            WHERE redeem_id=?
        """, (now(), order_id))
        db.commit()

    try:
        bot.send_message(user_id, f"✅ تمت الموافقة على طلبك #{order_id}")
    except:
        pass

    bot.send_message(ADMIN_ID, f"✅ تمت الموافقة على الطلب #{order_id}")

def reject_order(order_id):
    with db_lock:
        cursor.execute("""
            SELECT user_id, cost, status
            FROM redemptions
            WHERE redeem_id=?
        """, (order_id,))
        row = cursor.fetchone()

        if not row:
            bot.send_message(ADMIN_ID, "❌ الطلب غير موجود")
            return

        user_id, cost, status = row
        if status != "pending":
            bot.send_message(ADMIN_ID, "❌ هذا الطلب تمت معالجته من قبل")
            return

        cursor.execute("""
            UPDATE redemptions
            SET status='rejected', processed_at=?
            WHERE redeem_id=?
        """, (now(), order_id))
        db.commit()

    add_points(user_id, cost)

    try:
        bot.send_message(user_id, f"❌ تم رفض طلبك #{order_id} وتم إرجاع {cost} نقطة")
    except:
        pass

    bot.send_message(ADMIN_ID, f"✅ تم رفض الطلب #{order_id}")

@bot.message_handler(commands=['createcode'])
def createcode_cmd(message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) != 3 or not args[2].isdigit():
        bot.send_message(ADMIN_ID, "❌ /createcode CODE POINTS")
        return

    code = args[1].upper().strip()
    points = int(args[2])

    try:
        create_gift_code(code, points)
        bot.send_message(ADMIN_ID, f"✅ تم إنشاء الكود {code} بقيمة {points} نقطة")
    except sqlite3.IntegrityError:
        bot.send_message(ADMIN_ID, "❌ هذا الكود موجود من قبل")

@bot.message_handler(commands=['codeslist'])
def codeslist_cmd(message):
    if not is_admin(message.from_user.id):
        return

    with db_lock:
        cursor.execute("""
            SELECT code, points, max_uses, uses_count, active, created_at
            FROM gift_codes
            ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()

    if not rows:
        bot.send_message(ADMIN_ID, "📭 لا توجد أكواد")
        return

    text = "🎁 <b>أكواد الهدايا</b>\n\n"
    for code, points, max_uses, uses_count, active, created_at in rows:
        status = "✅ مفعّل" if active == 1 else "❌ معطل"
        limit_text = "غير محدود" if max_uses == 0 else str(max_uses)
        text += f"{code} | {points} نقطة | {uses_count}/{limit_text} | {status}\n"

    bot.send_message(ADMIN_ID, text)

@bot.message_handler(commands=['disablecode'])
def disablecode_cmd(message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) != 2:
        bot.send_message(ADMIN_ID, "❌ /disablecode CODE")
        return

    code = args[1].upper().strip()

    with db_lock:
        cursor.execute("UPDATE gift_codes SET active=0 WHERE code=?", (code,))
        db.commit()

    bot.send_message(ADMIN_ID, f"✅ تم تعطيل الكود {code}")

@bot.message_handler(commands=['backup'])
def backup_cmd(message):
    if not is_admin(message.from_user.id):
        return

    ensure_backup_dir()
    backup_name = os.path.join(BACKUP_DIR, f"manual_backup_{now()}.db")
    with db_lock:
        backup_db = sqlite3.connect(backup_name)
        db.backup(backup_db)
        backup_db.close()

    with open(backup_name, "rb") as f:
        bot.send_document(ADMIN_ID, f)

@bot.message_handler(commands=['export'])
def export_cmd(message):
    if not is_admin(message.from_user.id):
        return

    filename = f"users_export_{now()}.csv"

    with db_lock:
        cursor.execute("""
            SELECT user_id, username, full_name, referrer, points, joined_at
            FROM users
            ORDER BY points DESC, joined_at ASC
        """)
        rows = cursor.fetchall()

    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["user_id", "username", "full_name", "referrer", "points", "joined_at"])
        for row in rows:
            writer.writerow(row)

    with open(filename, "rb") as f:
        bot.send_document(ADMIN_ID, f)

# =========================
# CALLBACKS
# =========================
@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    user_id = call.from_user.id

    if not anti_spam(user_id):
        try:
            bot.answer_callback_query(call.id, "⏳ انتظر قليلاً")
        except:
            pass
        return

    if call.data == "check_sub":
        changed = sync_user_referral(user_id)
        points = get_points(user_id)

        if is_fully_subscribed(user_id):
            msg = f"✅ أنت مشترك في القناتين\n💰 نقاطك: {points}"
            if changed:
                msg += "\n🎉 تم تحديث نقاطك"
        else:
            msg = "❌ لازم تشترك في القناتين أولاً"

        bot.send_message(user_id, msg, reply_markup=make_user_buttons())

    elif call.data == "my_points":
        bot.send_message(user_id, f"💰 نقاطك: {get_points(user_id)}")

    elif call.data == "shop":
        send_shop(user_id)

    elif call.data == "my_link":
        me = bot.get_me()
        link = f"https://t.me/{me.username}?start={user_id}"
        bot.send_message(user_id, f"🔗 رابطك:\n<code>{link}</code>")

    elif call.data == "my_rank":
        rank = get_rank(user_id)
        if rank is None:
            bot.send_message(user_id, "❌ لا توجد بيانات")
        else:
            bot.send_message(user_id, f"🏆 ترتيبك: {rank} من {get_total_users()}\n💰 نقاطك: {get_points(user_id)}")

    elif call.data == "my_orders":
        send_orders(user_id)

    elif call.data == "my_invites":
        send_invited_list(user_id)

    elif call.data == "info":
        send_info(user_id)

    elif call.data.startswith("redeem_"):
        reward_id = int(call.data.split("_")[1])
        ok, msg = redeem_reward(user_id, reward_id)
        bot.send_message(user_id, msg)

    elif call.data == "admin_stats" and is_admin(user_id):
        with db_lock:
            cursor.execute("SELECT COUNT(*) FROM users")
            users_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM redemptions WHERE status='pending'")
            pending = cursor.fetchone()[0]
        bot.send_message(ADMIN_ID, f"📊 المستخدمون: {users_count}\n📦 الطلبات المعلقة: {pending}")

    elif call.data == "admin_top" and is_admin(user_id):
        with db_lock:
            cursor.execute("""
                SELECT user_id, username, full_name, points
                FROM users
                ORDER BY points DESC, joined_at ASC
                LIMIT 10
            """)
            rows = cursor.fetchall()

        if not rows:
            bot.send_message(ADMIN_ID, "📭 ماكانش بيانات")
        else:
            text = "🏆 <b>أفضل 10</b>\n\n"
            for i, (uid, username, full_name, points) in enumerate(rows, start=1):
                name = get_display_name(uid, username, full_name)
                text += f"{i}. {name}\n🆔 <code>{uid}</code> | 💰 {points}\n\n"
            bot.send_message(ADMIN_ID, text)

    elif call.data == "admin_orders" and is_admin(user_id):
        with db_lock:
            cursor.execute("""
                SELECT r.redeem_id, r.user_id, s.title, r.cost, r.status, r.created_at
                FROM redemptions r
                JOIN rewards_shop s ON r.reward_id = s.reward_id
                ORDER BY r.created_at DESC
                LIMIT 20
            """)
            rows = cursor.fetchall()

        if not rows:
            bot.send_message(ADMIN_ID, "📭 لا توجد طلبات.")
        else:
            for redeem_id, uid, title, cost, status, created_at in rows:
                bot.send_message(
                    ADMIN_ID,
                    f"📦 <b>طلب #{redeem_id}</b>\n👤 <code>{uid}</code>\n🎁 {title}\n💸 {cost}\n📌 {status}\n🕒 {fmt(created_at)}",
                    reply_markup=make_order_manage_buttons(redeem_id) if status == "pending" else None
                )

    elif call.data == "admin_shop" and is_admin(user_id):
        with db_lock:
            cursor.execute("""
                SELECT reward_id, title, cost, active
                FROM rewards_shop
                ORDER BY reward_id DESC
            """)
            rows = cursor.fetchall()

        if not rows:
            bot.send_message(ADMIN_ID, "📭 المتجر فارغ")
        else:
            for reward_id, title, cost, active in rows:
                status = "✅ مفعّل" if active == 1 else "❌ متوقف"
                bot.send_message(
                    ADMIN_ID,
                    f"🛍 <b>عنصر #{reward_id}</b>\n🎁 {title}\n💸 {cost} نقطة\n📌 {status}",
                    reply_markup=make_reward_manage_buttons(reward_id, active)
                )

    elif call.data == "admin_backup" and is_admin(user_id):
        ensure_backup_dir()
        backup_name = os.path.join(BACKUP_DIR, f"callback_backup_{now()}.db")
        with db_lock:
            backup_db = sqlite3.connect(backup_name)
            db.backup(backup_db)
            backup_db.close()

        with open(backup_name, "rb") as f:
            bot.send_document(ADMIN_ID, f)

    elif call.data == "admin_export" and is_admin(user_id):
        filename = f"users_export_{now()}.csv"

        with db_lock:
            cursor.execute("""
                SELECT user_id, username, full_name, referrer, points, joined_at
                FROM users
                ORDER BY points DESC, joined_at ASC
            """)
            rows = cursor.fetchall()

        with open(filename, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["user_id", "username", "full_name", "referrer", "points", "joined_at"])
            for row in rows:
                writer.writerow(row)

        with open(filename, "rb") as f:
            bot.send_document(ADMIN_ID, f)

    elif call.data == "admin_help_addpoints" and is_admin(user_id):
        bot.send_message(ADMIN_ID, "➕ الاستعمال:\n<code>/addpoints USER_ID AMOUNT</code>")

    elif call.data == "admin_help_removepoints" and is_admin(user_id):
        bot.send_message(ADMIN_ID, "➖ الاستعمال:\n<code>/removepoints USER_ID AMOUNT</code>")

    elif call.data == "admin_help_user" and is_admin(user_id):
        bot.send_message(ADMIN_ID, "👤 الاستعمال:\n<code>/user USER_ID</code>")

    elif call.data == "admin_help_broadcast" and is_admin(user_id):
        bot.send_message(ADMIN_ID, "📢 الاستعمال:\n<code>/broadcast نص الرسالة</code>")

    elif call.data == "admin_help_blacklist" and is_admin(user_id):
        bot.send_message(ADMIN_ID, "🚫 الاستعمال:\n<code>/blacklist USER_ID السبب</code>")

    elif call.data == "admin_help_unblacklist" and is_admin(user_id):
        bot.send_message(ADMIN_ID, "✅ الاستعمال:\n<code>/unblacklist USER_ID</code>")

    elif call.data == "admin_help_codes" and is_admin(user_id):
        bot.send_message(
            ADMIN_ID,
            "🎁 أوامر الأكواد:\n<code>/createcode CODE POINTS</code>\n<code>/codeslist</code>\n<code>/disablecode CODE</code>"
        )

    elif call.data.startswith("approve_order_") and is_admin(user_id):
        order_id = int(call.data.split("_")[2])
        approve_order(order_id)

    elif call.data.startswith("reject_order_") and is_admin(user_id):
        order_id = int(call.data.split("_")[2])
        reject_order(order_id)

    elif call.data.startswith("toggle_reward_") and is_admin(user_id):
        reward_id = int(call.data.split("_")[2])

        with db_lock:
            cursor.execute("SELECT active FROM rewards_shop WHERE reward_id=?", (reward_id,))
            row = cursor.fetchone()
            if row:
                new_active = 0 if row[0] == 1 else 1
                cursor.execute("UPDATE rewards_shop SET active=? WHERE reward_id=?", (new_active, reward_id))
                db.commit()

        bot.send_message(ADMIN_ID, f"✅ تم تغيير حالة العنصر #{reward_id}")

    elif call.data.startswith("edit_reward_") and is_admin(user_id):
        reward_id = int(call.data.split("_")[2])
        bot.send_message(ADMIN_ID, f"💲 لتعديل سعر العنصر #{reward_id} استعمل:\n<code>/editreward {reward_id} NEW_PRICE</code>")

# =========================
# PERIODIC SYNC
# =========================
def full_sync():
    with db_lock:
        cursor.execute("SELECT invited_user FROM referrals")
        rows = cursor.fetchall()

    for (invited_user,) in rows:
        try:
            fully_subscribed = is_fully_subscribed(invited_user)

            with db_lock:
                cursor.execute("SELECT counted FROM referrals WHERE invited_user=?", (invited_user,))
                row = cursor.fetchone()
                counted = row[0] if row else 0

            if fully_subscribed and counted == 0:
                count_referral(invited_user)
            elif (not fully_subscribed) and counted == 1:
                uncount_referral(invited_user)
        except:
            pass

def sync_loop():
    while True:
        try:
            full_sync()
        except:
            pass
        time.sleep(SYNC_INTERVAL_SECONDS)

threading.Thread(target=sync_loop, daemon=True).start()

print("Bot is running...")
bot.infinity_polling(skip_pending=True)