import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta, date
import json
import os
import sys
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

# ─── CONFIG (edit in .env) ────────────────────────────────────────────────────
TOKEN               = os.getenv("DISCORD_TOKEN")
GUILD_ID            = int(os.getenv("GUILD_ID", 0))
ANNOUNCE_CHANNEL_ID = int(os.getenv("ANNOUNCE_CHANNEL_ID", 0))
VOICE_CHANNEL_ID    = int(os.getenv("VOICE_CHANNEL_ID", 0))
STUDY_ROLE_NAME     = os.getenv("STUDY_ROLE_NAME", "study")
PING_ROLE_NAME      = os.getenv("PING_ROLE_NAME", "anh em cứu vớt tuong lai")
STUDY_ROLE_ID       = int(os.getenv("STUDY_ROLE_ID", 0))
PING_ROLE_ID        = int(os.getenv("PING_ROLE_ID", 0))
ANNOUNCE_HOUR       = int(os.getenv("ANNOUNCE_HOUR", 20))
ANNOUNCE_MINUTE     = int(os.getenv("ANNOUNCE_MINUTE", 0))
LATE_GRACE_MINUTES  = int(os.getenv("LATE_GRACE_MINUTES", 15))
TIMEZONE_OFFSET     = int(os.getenv("TIMEZONE_OFFSET", 7))
ATTENDANCE_FILE     = os.getenv("ATTENDANCE_FILE", "data/attendance.json")
# ─────────────────────────────────────────────────────────────────────────────

MIN_DURATION_SECONDS = 3600  # 1 hour minimum
MAX_LEAVES           = 3     # maximum leave count before disqualification
CHECKIN_EMOJI        = os.getenv("CHECKIN_EMOJI", "✅")  # react to announcement = present
MONTHLY_AWARD_HOUR   = int(os.getenv("MONTHLY_AWARD_HOUR", 21))   # end-of-month praise time
MONTHLY_AWARD_MINUTE = int(os.getenv("MONTHLY_AWARD_MINUTE", 0))

intents = discord.Intents.default()
intents.message_content = True
intents.members         = True
intents.voice_states    = True
intents.reactions       = True

bot       = commands.Bot(command_prefix="!", intents=intents)
GUILD_OBJ = discord.Object(id=GUILD_ID)

attendance_data: dict = {}

# In-memory: uid -> float timestamp of when they last joined the target VC
active_voice_sessions: dict[str, float] = {}


# ── helpers ───────────────────────────────────────────────────────────────────

def get_study_role(guild: discord.Guild):
    """Resolve the study role by ID (preferred) then fall back to name."""
    if STUDY_ROLE_ID:
        role = guild.get_role(STUDY_ROLE_ID)
        if role:
            return role
    return discord.utils.get(guild.roles, name=STUDY_ROLE_NAME)

def get_ping_role(guild: discord.Guild):
    """Resolve the ping role by ID (preferred) then fall back to name."""
    if PING_ROLE_ID:
        role = guild.get_role(PING_ROLE_ID)
        if role:
            return role
    return discord.utils.get(guild.roles, name=PING_ROLE_NAME)

def local_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=TIMEZONE_OFFSET)))

def today_key() -> str:
    return local_now().strftime("%Y-%m-%d")

def is_date_key(key: str) -> bool:
    try:
        datetime.strptime(key, "%Y-%m-%d")
        return True
    except ValueError:
        return False

def is_within_attendance_window() -> bool:
    now   = local_now()
    start = now.replace(hour=ANNOUNCE_HOUR, minute=ANNOUNCE_MINUTE, second=0, microsecond=0)
    return now >= start

def is_late(dt: datetime) -> bool:
    deadline = dt.replace(hour=ANNOUNCE_HOUR, minute=ANNOUNCE_MINUTE, second=0, microsecond=0)
    deadline += timedelta(minutes=LATE_GRACE_MINUTES)
    return dt > deadline

def load_data():
    global attendance_data
    os.makedirs(os.path.dirname(ATTENDANCE_FILE) or ".", exist_ok=True)
    if os.path.exists(ATTENDANCE_FILE):
        with open(ATTENDANCE_FILE, "r", encoding="utf-8") as f:
            attendance_data = json.load(f)
    attendance_data.setdefault("_streaks", {})
    attendance_data.setdefault("_meta", {})

def save_data():
    os.makedirs(os.path.dirname(ATTENDANCE_FILE) or ".", exist_ok=True)
    with open(ATTENDANCE_FILE, "w", encoding="utf-8") as f:
        json.dump(attendance_data, f, indent=2, ensure_ascii=False)

def ensure_today(guild: discord.Guild) -> str:
    key = today_key()
    if key not in attendance_data:
        study_role = get_study_role(guild)
        expected   = [str(m.id) for m in study_role.members if not m.bot] if study_role else []
        attendance_data[key] = {"expected": expected, "present": {}, "closed": False}
        save_data()
    return key

def _blank_entry(member: discord.Member, source: str, now: datetime) -> dict:
    return {
        "name":           member.display_name,
        "join_time":      now.strftime("%H:%M:%S"),
        "late":           is_late(now),
        "manual":         False,
        "source":         source,
        "total_duration": 0,
        "leave_count":    0,
        "disqualified":   False,
    }


# ── streak helpers ────────────────────────────────────────────────────────────

def _streak_record(uid: str) -> dict:
    return attendance_data["_streaks"].setdefault(uid, {
        "current_streak": 0,
        "last_attended":  None,
        "longest_streak": 0,
    })

def update_streak(uid: str, attended_date_str: str):
    rec      = _streak_record(uid)
    attended = date.fromisoformat(attended_date_str)
    last_str = rec.get("last_attended")

    if last_str is None:
        rec["current_streak"] = 1
    else:
        last = date.fromisoformat(last_str)
        gap  = (attended - last).days
        if gap <= 0:
            return  # already updated for this date
        elif gap <= 2:
            rec["current_streak"] += 1  # 2-day insurance: streak survives 1-day gap
        else:
            rec["current_streak"] = 1   # gap > 2 days: reset

    rec["last_attended"]  = attended_date_str
    rec["longest_streak"] = max(rec.get("longest_streak", 0), rec["current_streak"])
    save_data()

def get_current_streak(uid: str, as_of: str = None) -> int:
    rec = attendance_data.get("_streaks", {}).get(uid)  # read-only, don't create
    if not rec:
        return 0
    last_str = rec.get("last_attended")
    if not last_str:
        return 0
    ref  = date.fromisoformat(as_of) if as_of else date.fromisoformat(today_key())
    last = date.fromisoformat(last_str)
    if (ref - last).days > 2:
        return 0  # streak has expired
    return rec.get("current_streak", 0)

def get_longest_streak(uid: str) -> int:
    return attendance_data.get("_streaks", {}).get(uid, {}).get("longest_streak", 0)

def build_streak_leaderboard(guild: discord.Guild) -> list:
    """Return [(member, current_streak, longest_streak), ...] sorted high→low."""
    study_role = get_study_role(guild)
    members    = [m for m in study_role.members if not m.bot] if study_role else []
    ranking    = [
        (m, get_current_streak(str(m.id)), get_longest_streak(str(m.id)))
        for m in members
    ]
    ranking.sort(key=lambda x: (x[1], x[2]), reverse=True)
    return ranking

def build_award_embed(guild: discord.Guild, when: datetime):
    """Build the monthly hall-of-fame embed, or None if nobody has a streak."""
    ranking = [r for r in build_streak_leaderboard(guild) if r[1] > 0]
    if not ranking:
        return None

    top_streak = ranking[0][1]
    winners    = [r for r in ranking if r[1] == top_streak]
    medals     = ["🥇", "🥈", "🥉"]

    board = []
    for i, (m, streak, longest) in enumerate(ranking[:10]):
        rank = medals[i] if i < 3 else f"`#{i + 1}`"
        board.append(f"{rank} **{m.display_name}** — 🔥 {streak} ngày (kỷ lục: {longest})")

    embed = discord.Embed(
        title=f"🏆 BẢNG VÀNG STREAK — Tháng {when.strftime('%m/%Y')}",
        description=(
            "Chúc mừng những chiến binh kiên trì nhất tháng này! 🎉\n"
            "Sự đều đặn của các bạn là tấm gương cho cả nhóm. 💪"
        ),
        color=discord.Color.gold(),
    )
    winner_mentions = ", ".join(w[0].mention for w in winners)
    embed.add_field(
        name="👑 Quán quân Streak",
        value=f"{winner_mentions}\n🔥 **{top_streak} ngày** liên tiếp — xuất sắc!",
        inline=False,
    )
    embed.add_field(name="📊 Bảng xếp hạng", value="\n".join(board), inline=False)
    embed.set_footer(text="Tiếp tục giữ vững phong độ cho tháng sau nhé!")
    return embed


# ── voice session helpers ─────────────────────────────────────────────────────

def get_live_duration(uid: str) -> int:
    join_ts = active_voice_sessions.get(uid)
    if join_ts is None:
        return 0
    return int(local_now().timestamp() - join_ts)

def is_qualified(uid: str, key: str) -> bool:
    info = attendance_data.get(key, {}).get("present", {}).get(uid)
    if info is None:
        return False
    if info.get("manual"):
        return True
    total = info.get("total_duration", 0)
    if key == today_key():
        total += get_live_duration(uid)
    return total >= MIN_DURATION_SECONDS and not info.get("disqualified", False)


# ── startup ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    load_data()
    if not daily_announce.is_running():
        daily_announce.start()
    if not monthly_award.is_running():
        monthly_award.start()
    bot.tree.copy_global_to(guild=GUILD_OBJ)
    await bot.tree.sync(guild=GUILD_OBJ)
    print(f"✅  {bot.user} is online!")
    print(f"   Slash commands synced to guild {GUILD_ID}")
    print(f"   Announce at {ANNOUNCE_HOUR:02d}:{ANNOUNCE_MINUTE:02d} UTC+{TIMEZONE_OFFSET}")


# ── daily announcement ────────────────────────────────────────────────────────

@tasks.loop(minutes=1)
async def daily_announce():
    now = local_now()
    if now.hour != ANNOUNCE_HOUR or now.minute != ANNOUNCE_MINUTE:
        return

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    channel    = guild.get_channel(ANNOUNCE_CHANNEL_ID)
    vc         = guild.get_channel(VOICE_CHANNEL_ID)
    study_role = get_study_role(guild)
    ping_role  = get_ping_role(guild)

    if not channel:
        return

    key          = ensure_today(guild)
    vc_mention   = vc.mention if vc else "**phòng học**"
    ping_mention = ping_role.mention if ping_role else f"@{PING_ROLE_NAME}"
    role_mention = study_role.mention if study_role else "@everyone"

    embed = discord.Embed(
        title="📚 Đã đến giờ học!",
        description=(
            f"Xin chào {role_mention}!\n\n"
            f"🕗 Bây giờ là **{ANNOUNCE_HOUR:02d}:{ANNOUNCE_MINUTE:02d}** — hãy vào {vc_mention} để điểm danh.\n"
            f"⏰ Điểm danh sau **{LATE_GRACE_MINUTES} phút** sẽ bị ghi muộn.\n\n"
            f"📋 **Quy định buổi học:**\n"
            f"• Ở trong phòng tối thiểu **1 tiếng** (3600 giây)\n"
            f"• Ra/vào phòng tối đa **{MAX_LEAVES} lần**\n"
            f"• Vượt quá {MAX_LEAVES} lần rời phòng → bị đánh **vắng mặt**\n\n"
            f"👉 Thả cảm xúc {CHECKIN_EMOJI} vào tin nhắn này để **điểm danh** ngay!\n"
            f"Dùng `/myattendance` để xem streak của bạn."
        ),
        color=discord.Color.blurple()
    )
    embed.set_footer(text=f"Ngày {key} — Bot Điểm Danh")
    msg = await channel.send(content=ping_mention, embed=embed)

    # Remember this message so reactions on it count as check-ins
    attendance_data[key]["announce_message_id"] = msg.id
    save_data()
    try:
        await msg.add_reaction(CHECKIN_EMOJI)
    except discord.HTTPException:
        pass


# ── monthly streak award ──────────────────────────────────────────────────────

@tasks.loop(minutes=1)
async def monthly_award():
    now = local_now()
    if now.hour != MONTHLY_AWARD_HOUR or now.minute != MONTHLY_AWARD_MINUTE:
        return
    # Fire only on the last day of the month
    if (now + timedelta(days=1)).month == now.month:
        return

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    month_key = now.strftime("%Y-%m")
    meta      = attendance_data.setdefault("_meta", {})
    if meta.get("last_award_month") == month_key:
        return  # already awarded this month

    channel = guild.get_channel(ANNOUNCE_CHANNEL_ID)
    if not channel:
        return

    embed = build_award_embed(guild, now)
    if embed is None:
        return

    ping_role    = get_ping_role(guild)
    ping_mention = ping_role.mention if ping_role else ""
    await channel.send(content=f"🏆 **VINH DANH CUỐI THÁNG!** 🏆 {ping_mention}", embed=embed)

    meta["last_award_month"] = month_key
    save_data()


# ── auto check-in via text message ───────────────────────────────────────────

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    if message.guild.id != GUILD_ID:
        return
    if not is_within_attendance_window():
        return

    member     = message.author
    study_role = get_study_role(member.guild)
    if not study_role or study_role not in member.roles:
        return

    key = ensure_today(member.guild)
    if attendance_data[key].get("closed"):
        return

    uid = str(member.id)
    if uid in attendance_data[key]["present"]:
        return  # already marked, skip

    now = local_now()
    attendance_data[key]["present"][uid] = _blank_entry(member, "text", now)
    if uid not in attendance_data[key]["expected"]:
        attendance_data[key]["expected"].append(uid)
    save_data()


# ── auto check-in via reaction on announcement ────────────────────────────────

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.guild_id != GUILD_ID:
        return
    if payload.user_id == bot.user.id:
        return  # ignore the bot's own seed reaction
    if str(payload.emoji) != CHECKIN_EMOJI:
        return

    key = today_key()
    day = attendance_data.get(key)
    if not day:
        return
    # Only the daily announcement message counts
    if payload.message_id != day.get("announce_message_id"):
        return
    if day.get("closed"):
        return

    member = payload.member
    if member is None or member.bot:
        return

    study_role = get_study_role(member.guild)
    if not study_role or study_role not in member.roles:
        return

    uid = str(member.id)
    if uid in day["present"]:
        return  # already marked

    now = local_now()
    day["present"][uid] = _blank_entry(member, "reaction", now)
    if uid not in day["expected"]:
        day["expected"].append(uid)
    save_data()

    try:
        await member.send(
            f"✅ Đã ghi nhận điểm danh của bạn lúc **{now.strftime('%H:%M')}** "
            f"(qua reaction)!\nĐừng quên vào phòng học đủ **1 tiếng** để đạt yêu cầu nhé."
        )
    except discord.Forbidden:
        pass


# ── voice tracking ────────────────────────────────────────────────────────────

@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):
    if member.bot or member.guild.id != GUILD_ID:
        return

    study_role = get_study_role(member.guild)
    if not study_role or study_role not in member.roles:
        return

    uid       = str(member.id)
    target_id = VOICE_CHANNEL_ID
    joined_vc = after.channel and after.channel.id == target_id
    left_vc   = (
        before.channel and before.channel.id == target_id
        and (not after.channel or after.channel.id != target_id)
    )

    if joined_vc:
        active_voice_sessions[uid] = local_now().timestamp()

        key = ensure_today(member.guild)
        if attendance_data[key].get("closed"):
            return

        now = local_now()
        if uid not in attendance_data[key]["present"]:
            attendance_data[key]["present"][uid] = _blank_entry(member, "voice", now)
            if uid not in attendance_data[key]["expected"]:
                attendance_data[key]["expected"].append(uid)
            save_data()

    elif left_vc:
        key     = today_key()
        join_ts = active_voice_sessions.pop(uid, None)

        if join_ts and key in attendance_data and uid in attendance_data[key]["present"]:
            info         = attendance_data[key]["present"][uid]
            session_secs = int(local_now().timestamp() - join_ts)
            info["total_duration"] = info.get("total_duration", 0) + session_secs
            info["leave_count"]    = info.get("leave_count", 0) + 1
            leave_count            = info["leave_count"]

            if leave_count > MAX_LEAVES:
                info["disqualified"] = True
                save_data()
                try:
                    await member.send(
                        f"⛔ Bạn đã rời phòng học **{leave_count} lần** (giới hạn: {MAX_LEAVES} lần).\n"
                        f"Bạn đã bị đánh **vắng mặt** cho ngày hôm nay do vi phạm quy định."
                    )
                except discord.Forbidden:
                    pass
            else:
                save_data()
                remaining = MAX_LEAVES - leave_count
                try:
                    await member.send(
                        f"⚠️ Cảnh báo: Bạn vừa rời phòng học. (Lần thứ **{leave_count}/{MAX_LEAVES}**)\n"
                        f"Còn **{remaining}** lần rời phòng trước khi bị đánh vắng mặt."
                    )
                except discord.Forbidden:
                    pass


# ── streak finalization ───────────────────────────────────────────────────────

async def finalize_day_streaks(key: str):
    for uid in list(attendance_data.get(key, {}).get("present", {})):
        if is_qualified(uid, key):
            update_streak(uid, key)


# ── slash commands ────────────────────────────────────────────────────────────

@bot.tree.command(
    name="attendance",
    description="Xem điểm danh hôm nay hoặc ngày cụ thể (admin)",
    guild=GUILD_OBJ,
)
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(date="Ngày cần xem YYYY-MM-DD, để trống = hôm nay")
async def slash_attendance(interaction: discord.Interaction, date: str = None):
    key   = date or today_key()
    guild = interaction.guild

    if key not in attendance_data or not is_date_key(key):
        await interaction.response.send_message(
            f"❌ Không có dữ liệu điểm danh cho ngày `{key}`.", ephemeral=True
        )
        return

    data         = attendance_data[key]
    present_data = data["present"]
    expected_ids = data["expected"]
    is_today     = key == today_key()

    qualified_lines   = []
    unqualified_lines = []
    absent_lines      = []

    for uid, info in present_data.items():
        total        = info.get("total_duration", 0)
        if is_today:
            total += get_live_duration(uid)
        minutes      = total // 60
        leave_count  = info.get("leave_count", 0)
        disqualified = info.get("disqualified", False)
        streak       = get_current_streak(uid, key)
        late_tag     = " ⏰" if info.get("late") else ""
        manual_tag   = " ✏️" if info.get("manual") else ""

        if info.get("manual") or (total >= MIN_DURATION_SECONDS and not disqualified):
            qualified_lines.append(
                f"✅ **{info['name']}**{late_tag}{manual_tag} — {minutes} phút | 🔥 {streak} ngày"
            )
        else:
            reason = "vi phạm ra/vào" if disqualified else f"chỉ {minutes} phút"
            unqualified_lines.append(
                f"⚠️ **{info['name']}**{late_tag} — {reason} | Ra/vào: {leave_count}x"
            )

    for uid in expected_ids:
        if uid not in present_data:
            m    = guild.get_member(int(uid))
            name = m.display_name if m else f"(id:{uid})"
            absent_lines.append(f"❌ {name}")

    closed_tag = " 🔒 (đã đóng)" if data.get("closed") else ""
    embed = discord.Embed(
        title=f"📋 Điểm danh — {key}{closed_tag}",
        color=discord.Color.green() if not absent_lines and not unqualified_lines else discord.Color.orange(),
    )
    embed.add_field(
        name=f"✅ Đạt yêu cầu ({len(qualified_lines)})",
        value="\n".join(qualified_lines) or "_Chưa có ai_",
        inline=False,
    )
    embed.add_field(
        name=f"⚠️ Chưa đủ điều kiện / Vi phạm ({len(unqualified_lines)})",
        value="\n".join(unqualified_lines) or "_Không có_",
        inline=False,
    )
    embed.add_field(
        name=f"❌ Vắng mặt hoàn toàn ({len(absent_lines)})",
        value="\n".join(absent_lines) or "_Không ai vắng_ 🎉",
        inline=False,
    )
    embed.set_footer(text="✏️ = thủ công  |  ⏰ = muộn  |  🔥 = streak hiện tại")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="myattendance",
    description="Xem lịch sử điểm danh và streak của bạn",
    guild=GUILD_OBJ,
)
@app_commands.describe(days="Số ngày gần nhất (mặc định: 7)")
async def slash_my_attendance(interaction: discord.Interaction, days: int = 7):
    uid  = str(interaction.user.id)
    keys = sorted([k for k in attendance_data if is_date_key(k)], reverse=True)[:days]

    lines = []
    for key in keys:
        data    = attendance_data[key]
        present = data["present"]
        if uid in present:
            info     = present[uid]
            total    = info.get("total_duration", 0)
            if key == today_key():
                total += get_live_duration(uid)
            minutes   = total // 60
            late_tag  = " ⏰" if info.get("late") else ""
            status    = "✅" if is_qualified(uid, key) else "⚠️"
            lines.append(f"{status} **{key}** — {info['join_time']}{late_tag} | {minutes} phút")
        elif uid in data.get("expected", []):
            lines.append(f"❌ **{key}** — Vắng mặt")

    if not lines:
        await interaction.response.send_message(
            "Không tìm thấy dữ liệu điểm danh của bạn.", ephemeral=True
        )
        return

    qualified  = sum(1 for l in lines if l.startswith("✅"))
    total_days = len(lines)
    rate       = int(qualified / total_days * 100) if total_days else 0
    streak     = get_current_streak(uid)
    rec        = _streak_record(uid)
    longest    = rec.get("longest_streak", 0)

    embed = discord.Embed(
        title=f"📊 Lịch sử điểm danh — {interaction.user.display_name}",
        description="\n".join(lines),
        color=discord.Color.blue(),
    )
    embed.add_field(name="🔥 Streak hiện tại", value=f"**{streak} ngày** liên tiếp", inline=True)
    embed.add_field(name="🏆 Streak dài nhất", value=f"**{longest} ngày**", inline=True)
    embed.add_field(
        name="📈 Tỉ lệ đạt yêu cầu",
        value=f"{qualified}/{total_days} ngày ({rate}%)",
        inline=True,
    )
    embed.set_footer(text="✅ = đạt  |  ⚠️ = có mặt nhưng chưa đủ điều kiện  |  ❌ = vắng")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="mark",
    description="Điểm danh thủ công cho thành viên (admin)",
    guild=GUILD_OBJ,
)
@app_commands.default_permissions(manage_roles=True)
@app_commands.describe(member="Thành viên cần điểm danh")
async def slash_mark(interaction: discord.Interaction, member: discord.Member):
    key = ensure_today(interaction.guild)
    uid = str(member.id)
    now = local_now()

    attendance_data[key]["present"][uid] = {
        "name":           member.display_name,
        "join_time":      now.strftime("%H:%M:%S"),
        "late":           False,
        "manual":         True,
        "source":         "manual",
        "total_duration": MIN_DURATION_SECONDS,
        "leave_count":    0,
        "disqualified":   False,
    }
    if uid not in attendance_data[key]["expected"]:
        attendance_data[key]["expected"].append(uid)

    update_streak(uid, key)
    save_data()
    await interaction.response.send_message(f"✅ Đã điểm danh thủ công cho **{member.display_name}**.")


@bot.tree.command(
    name="unmark",
    description="Xoá điểm danh của thành viên hôm nay (admin)",
    guild=GUILD_OBJ,
)
@app_commands.default_permissions(manage_roles=True)
@app_commands.describe(member="Thành viên cần xoá điểm danh")
async def slash_unmark(interaction: discord.Interaction, member: discord.Member):
    key = today_key()
    uid = str(member.id)

    if key in attendance_data and uid in attendance_data[key]["present"]:
        del attendance_data[key]["present"][uid]
        save_data()
        await interaction.response.send_message(f"🗑️ Đã xoá điểm danh của **{member.display_name}** hôm nay.")
    else:
        await interaction.response.send_message(
            f"❌ **{member.display_name}** chưa được điểm danh hôm nay.", ephemeral=True
        )


@bot.tree.command(
    name="initdd",
    description="Khởi tạo danh sách điểm danh hôm nay từ role (admin)",
    guild=GUILD_OBJ,
)
@app_commands.default_permissions(manage_roles=True)
async def slash_initdd(interaction: discord.Interaction):
    guild      = interaction.guild
    study_role = get_study_role(guild)

    if not study_role:
        await interaction.response.send_message(
            f"❌ Không tìm thấy role `{STUDY_ROLE_NAME}`.", ephemeral=True
        )
        return

    key      = today_key()
    expected = [str(m.id) for m in study_role.members if not m.bot]
    attendance_data[key] = {
        "expected": expected,
        "present":  attendance_data.get(key, {}).get("present", {}),
        "closed":   False,
    }
    save_data()
    await interaction.response.send_message(
        f"✅ Khởi tạo điểm danh ngày **{key}** với **{len(expected)}** thành viên có role `{STUDY_ROLE_NAME}`."
    )


@bot.tree.command(
    name="closedd",
    description="Đóng điểm danh và cập nhật streak hôm nay (admin)",
    guild=GUILD_OBJ,
)
@app_commands.default_permissions(manage_roles=True)
async def slash_closedd(interaction: discord.Interaction):
    key = today_key()
    if key not in attendance_data:
        await interaction.response.send_message("❌ Chưa có dữ liệu điểm danh hôm nay.", ephemeral=True)
        return

    # Flush any still-active voice sessions before closing
    for uid, join_ts in list(active_voice_sessions.items()):
        if uid in attendance_data[key]["present"]:
            info         = attendance_data[key]["present"][uid]
            session_secs = int(local_now().timestamp() - join_ts)
            info["total_duration"] = info.get("total_duration", 0) + session_secs
    active_voice_sessions.clear()

    attendance_data[key]["closed"] = True
    save_data()
    await finalize_day_streaks(key)

    await interaction.response.send_message(
        f"🔒 Đã đóng điểm danh ngày **{key}** và cập nhật streak cho tất cả thành viên đạt yêu cầu."
    )


@bot.tree.command(
    name="summary",
    description="Tổng hợp tỉ lệ đi học của tất cả thành viên (admin)",
    guild=GUILD_OBJ,
)
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(days="Số ngày gần nhất (mặc định: 7)")
async def slash_summary(interaction: discord.Interaction, days: int = 7):
    guild      = interaction.guild
    study_role = get_study_role(guild)
    members    = [m for m in study_role.members if not m.bot] if study_role else []
    keys       = sorted([k for k in attendance_data if is_date_key(k)], reverse=True)[:days]

    if not keys:
        await interaction.response.send_message("Không có dữ liệu điểm danh nào.", ephemeral=True)
        return

    lines = []
    for member in sorted(members, key=lambda m: m.display_name.lower()):
        uid      = str(member.id)
        attended = sum(1 for k in keys if is_qualified(uid, k))
        expected = sum(1 for k in keys if uid in attendance_data[k].get("expected", []))
        rate     = int(attended / expected * 100) if expected else 0
        bar      = "█" * (rate // 10) + "░" * (10 - rate // 10)
        streak   = get_current_streak(uid)
        streak_s = f"🔥{streak}" if streak > 0 else "💤0"
        lines.append(f"`{bar}` {rate:3d}% {streak_s} **{member.display_name}** ({attended}/{expected})")

    embed = discord.Embed(
        title=f"📈 Tổng hợp điểm danh — {days} ngày gần nhất",
        description="\n".join(lines) or "_Không có dữ liệu_",
        color=discord.Color.gold(),
    )
    embed.set_footer(text=" | ".join(keys))
    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="topstreak",
    description="Xem bảng xếp hạng streak hiện tại của cả nhóm",
    guild=GUILD_OBJ,
)
async def slash_topstreak(interaction: discord.Interaction):
    ranking = [r for r in build_streak_leaderboard(interaction.guild) if r[1] > 0]
    if not ranking:
        await interaction.response.send_message(
            "Chưa có ai có streak nào. Hãy là người đầu tiên! 🔥", ephemeral=True
        )
        return

    medals = ["🥇", "🥈", "🥉"]
    lines  = []
    for i, (m, streak, longest) in enumerate(ranking[:15]):
        rank = medals[i] if i < 3 else f"`#{i + 1}`"
        lines.append(f"{rank} **{m.display_name}** — 🔥 {streak} ngày (kỷ lục: {longest})")

    embed = discord.Embed(
        title="🔥 Bảng xếp hạng Streak",
        description="\n".join(lines),
        color=discord.Color.orange(),
    )
    embed.set_footer(text="🏆 Người dẫn đầu cuối tháng sẽ được vinh danh!")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="award",
    description="Đăng vinh danh streak ngay bây giờ (admin)",
    guild=GUILD_OBJ,
)
@app_commands.default_permissions(manage_messages=True)
async def slash_award(interaction: discord.Interaction):
    embed = build_award_embed(interaction.guild, local_now())
    if embed is None:
        await interaction.response.send_message(
            "Chưa có ai có streak để vinh danh.", ephemeral=True
        )
        return
    await interaction.response.send_message(content="🏆 **VINH DANH STREAK!** 🏆", embed=embed)


@bot.tree.command(
    name="ddhelp",
    description="Xem hướng dẫn sử dụng bot điểm danh",
    guild=GUILD_OBJ,
)
async def slash_ddhelp(interaction: discord.Interaction):
    embed = discord.Embed(title="📖 Hướng dẫn Bot Điểm Danh", color=discord.Color.purple())
    embed.add_field(
        name="👤 Lệnh cho thành viên",
        value=(
            "`/myattendance [số ngày]` — Xem lịch sử & streak của bạn\n"
            "`/topstreak` — Bảng xếp hạng streak cả nhóm\n"
            "`/ddhelp` — Hiển thị hướng dẫn này"
        ),
        inline=False,
    )
    embed.add_field(
        name="🔧 Lệnh cho admin",
        value=(
            "`/attendance [ngày]` — Xem điểm danh hôm nay (hoặc ngày YYYY-MM-DD)\n"
            "`/mark @thành-viên` — Điểm danh thủ công\n"
            "`/unmark @thành-viên` — Xoá điểm danh\n"
            "`/initdd` — Khởi tạo danh sách điểm danh hôm nay\n"
            "`/closedd` — Đóng điểm danh & cập nhật streak\n"
            "`/summary [số ngày]` — Tổng hợp tỉ lệ đi học\n"
            "`/award` — Đăng vinh danh streak ngay"
        ),
        inline=False,
    )
    embed.add_field(
        name="📋 Quy định điểm danh",
        value=(
            f"• Ở trong phòng tối thiểu **1 tiếng** (3600 giây)\n"
            f"• Ra/vào phòng tối đa **{MAX_LEAVES} lần**\n"
            f"• Streak không bị reset nếu chỉ nghỉ **≤ 2 ngày** liên tiếp\n"
            f"• Chat trong server (sau giờ học) cũng được tính điểm danh\n"
            f"• 🏆 Cuối tháng vinh danh người có streak cao nhất!"
        ),
        inline=False,
    )
    embed.set_footer(
        text=f"Bot tự động điểm danh khi vào voice channel hoặc chat sau {ANNOUNCE_HOUR:02d}:{ANNOUNCE_MINUTE:02d}."
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── error handling ────────────────────────────────────────────────────────────

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ Bạn không có quyền dùng lệnh này.", ephemeral=True)
    else:
        await interaction.response.send_message(f"⚠️ Lỗi: `{error}`", ephemeral=True)


# ─────────────────────────────────────────────────────────────────────────────
bot.run(TOKEN)
