import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
import json
import os
import sys
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

# ─── CONFIG (edit in .env) ───────────────────────────────────────────────────
TOKEN               = os.getenv("DISCORD_TOKEN")
GUILD_ID            = int(os.getenv("GUILD_ID", 0))
ANNOUNCE_CHANNEL_ID = int(os.getenv("ANNOUNCE_CHANNEL_ID", 0))
VOICE_CHANNEL_ID    = int(os.getenv("VOICE_CHANNEL_ID", 0))
STUDY_ROLE_NAME     = os.getenv("STUDY_ROLE_NAME", "study")
ANNOUNCE_HOUR       = int(os.getenv("ANNOUNCE_HOUR", 20))   # 8 PM
ANNOUNCE_MINUTE     = int(os.getenv("ANNOUNCE_MINUTE", 0))
LATE_GRACE_MINUTES  = int(os.getenv("LATE_GRACE_MINUTES", 15))  # after 8:15 = late
TIMEZONE_OFFSET     = int(os.getenv("TIMEZONE_OFFSET", 7))  # UTC+7 Vietnam
ATTENDANCE_FILE     = os.getenv("ATTENDANCE_FILE", "attendance.json")
COMMAND_PREFIX      = os.getenv("COMMAND_PREFIX", "!")
# ─────────────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix=commands.when_mentioned_or(COMMAND_PREFIX), intents=intents)

attendance_data: dict = {}  # { "YYYY-MM-DD": { expected, present, closed } }


# ── helpers ──────────────────────────────────────────────────────────────────

def local_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=TIMEZONE_OFFSET)))

def today_key() -> str:
    return local_now().strftime("%Y-%m-%d")

def load_data():
    global attendance_data
    if os.path.exists(ATTENDANCE_FILE):
        with open(ATTENDANCE_FILE, "r", encoding="utf-8") as f:
            attendance_data = json.load(f)

def save_data():
    with open(ATTENDANCE_FILE, "w", encoding="utf-8") as f:
        json.dump(attendance_data, f, indent=2, ensure_ascii=False)

def ensure_today(guild: discord.Guild):
    """Make sure today's attendance record exists."""
    key = today_key()
    if key not in attendance_data:
        study_role = discord.utils.get(guild.roles, name=STUDY_ROLE_NAME)
        expected = []
        if study_role:
            expected = [str(m.id) for m in study_role.members if not m.bot]
        attendance_data[key] = {
            "expected": expected,
            "present":  {},   # { member_id: { name, join_time, late, manual } }
            "closed":   False
        }
        save_data()
    return key

def is_late(dt: datetime) -> bool:
    deadline = dt.replace(hour=ANNOUNCE_HOUR, minute=ANNOUNCE_MINUTE, second=0, microsecond=0)
    deadline += timedelta(minutes=LATE_GRACE_MINUTES)
    return dt > deadline


# ── startup ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    load_data()
    if not daily_announce.is_running():
        daily_announce.start()
    print(f"✅  {bot.user} is online! Prefix: !")
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

    channel   = guild.get_channel(ANNOUNCE_CHANNEL_ID)
    vc        = guild.get_channel(VOICE_CHANNEL_ID)
    role      = discord.utils.get(guild.roles, name=STUDY_ROLE_NAME)

    if not channel or not role:
        return

    key = ensure_today(guild)    # creates today's record

    vc_mention = vc.mention if vc else "**phòng học**"

    embed = discord.Embed(
        title="📚 Đã đến giờ học!",
        description=(
            f"Xin chào {role.mention}!\n\n"
            f"🕗 Bây giờ là **{ANNOUNCE_HOUR:02d}:{ANNOUNCE_MINUTE:02d}** — hãy vào {vc_mention} để điểm danh.\n"
            f"⏰ Điểm danh sau **{LATE_GRACE_MINUTES} phút** sẽ bị ghi muộn.\n\n"
            f"Dùng `{COMMAND_PREFIX}attendance` để xem danh sách điểm danh."
        ),
        color=discord.Color.blurple()
    )
    embed.set_footer(text=f"Ngày {key} — Bot Điểm Danh")
    await channel.send(embed=embed)


# ── voice tracking ─────────────────────────────────────────────────────────

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if not after.channel or after.channel.id != VOICE_CHANNEL_ID:
        return
    if member.bot:
        return

    study_role = discord.utils.get(member.guild.roles, name=STUDY_ROLE_NAME)
    if not study_role or study_role not in member.roles:
        return

    key = ensure_today(member.guild)
    if attendance_data[key].get("closed"):
        return   # attendance closed

    member_id = str(member.id)
    if member_id in attendance_data[key]["present"]:
        return   # already marked

    now = local_now()
    attendance_data[key]["present"][member_id] = {
        "name":      member.display_name,
        "join_time": now.strftime("%H:%M:%S"),
        "late":      is_late(now),
        "manual":    False
    }

    # Add to expected if not there (joined after init)
    if member_id not in attendance_data[key]["expected"]:
        attendance_data[key]["expected"].append(member_id)

    save_data()

    # Optional: send a small DM confirmation
    try:
        late_note = " (muộn ⏰)" if is_late(now) else ""
        await member.send(f"✅ Đã ghi nhận điểm danh của bạn lúc **{now.strftime('%H:%M')}**{late_note}!")
    except discord.Forbidden:
        pass


# ── commands ──────────────────────────────────────────────────────────────────

@bot.command(name="attendance", aliases=["dd"])
@commands.has_permissions(manage_messages=True)
async def cmd_attendance(ctx: commands.Context, date: str = None):
    """Show attendance for today (or a given YYYY-MM-DD date). Admin only."""
    key = date or today_key()
    guild = ctx.guild

    if key not in attendance_data:
        await ctx.send(f"❌ Không có dữ liệu điểm danh cho ngày `{key}`.")
        return

    data    = attendance_data[key]
    present = data["present"]
    expected_ids = data["expected"]

    present_lines = []
    for uid, info in present.items():
        late_tag = " ⏰" if info.get("late") else ""
        manual   = " ✏️" if info.get("manual") else ""
        present_lines.append(f"✅ **{info['name']}** — {info['join_time']}{late_tag}{manual}")

    absent_lines = []
    for uid in expected_ids:
        if uid not in present:
            m = guild.get_member(int(uid))
            name = m.display_name if m else f"(id:{uid})"
            absent_lines.append(f"❌ {name}")

    total    = len(expected_ids)
    n_pres   = len(present_lines)
    n_abs    = len(absent_lines)
    n_late   = sum(1 for v in present.values() if v.get("late"))
    closed   = " 🔒 (đã đóng)" if data.get("closed") else ""

    embed = discord.Embed(
        title=f"📋 Điểm danh — {key}{closed}",
        color=discord.Color.green() if n_abs == 0 else discord.Color.orange()
    )
    embed.add_field(
        name=f"Có mặt ({n_pres}/{total}) — Muộn: {n_late}",
        value="\n".join(present_lines) or "_Chưa có ai_",
        inline=False
    )
    embed.add_field(
        name=f"Vắng mặt ({n_abs})",
        value="\n".join(absent_lines) or "_Không ai vắng_ 🎉",
        inline=False
    )
    embed.set_footer(text="✏️ = điểm danh thủ công  |  ⏰ = muộn")
    await ctx.send(embed=embed)


@bot.command(name="myattendance", aliases=["mydd"])
async def cmd_my_attendance(ctx: commands.Context, days: int = 7):
    """Show your own attendance history for the last N days."""
    uid    = str(ctx.author.id)
    lines  = []
    keys   = sorted(attendance_data.keys(), reverse=True)[:days]

    for key in keys:
        data    = attendance_data[key]
        present = data["present"]
        expected = data["expected"]

        if uid in present:
            info     = present[uid]
            late_tag = " ⏰" if info.get("late") else ""
            lines.append(f"✅ **{key}** — {info['join_time']}{late_tag}")
        elif uid in expected:
            lines.append(f"❌ **{key}** — Vắng mặt")
        # else: wasn't expected that day → skip

    if not lines:
        await ctx.send("Không tìm thấy dữ liệu điểm danh của bạn.")
        return

    attended = sum(1 for l in lines if l.startswith("✅"))
    total    = len(lines)
    rate     = int(attended / total * 100) if total else 0

    embed = discord.Embed(
        title=f"📊 Lịch sử điểm danh — {ctx.author.display_name}",
        description="\n".join(lines),
        color=discord.Color.blue()
    )
    embed.set_footer(text=f"Tỉ lệ đi học: {attended}/{total} ngày ({rate}%)")
    await ctx.send(embed=embed)


@bot.command(name="mark")
@commands.has_permissions(manage_roles=True)
async def cmd_mark(ctx: commands.Context, member: discord.Member):
    """Manually mark a member as present today. Admin only."""
    key = ensure_today(ctx.guild)
    uid = str(member.id)
    now = local_now()

    attendance_data[key]["present"][uid] = {
        "name":      member.display_name,
        "join_time": now.strftime("%H:%M:%S"),
        "late":      False,
        "manual":    True
    }
    if uid not in attendance_data[key]["expected"]:
        attendance_data[key]["expected"].append(uid)

    save_data()
    await ctx.send(f"✅ Đã điểm danh thủ công cho **{member.display_name}**.")


@bot.command(name="unmark")
@commands.has_permissions(manage_roles=True)
async def cmd_unmark(ctx: commands.Context, member: discord.Member):
    """Remove a member's attendance for today. Admin only."""
    key = today_key()
    uid = str(member.id)

    if key in attendance_data and uid in attendance_data[key]["present"]:
        del attendance_data[key]["present"][uid]
        save_data()
        await ctx.send(f"🗑️ Đã xoá điểm danh của **{member.display_name}** hôm nay.")
    else:
        await ctx.send(f"❌ **{member.display_name}** chưa được điểm danh hôm nay.")


@bot.command(name="initdd")
@commands.has_permissions(manage_roles=True)
async def cmd_init(ctx: commands.Context):
    """Manually initialise today's attendance with all 'study' role members."""
    guild      = ctx.guild
    study_role = discord.utils.get(guild.roles, name=STUDY_ROLE_NAME)

    if not study_role:
        await ctx.send(f"❌ Không tìm thấy role `{STUDY_ROLE_NAME}`.")
        return

    key = today_key()
    expected = [str(m.id) for m in study_role.members if not m.bot]
    attendance_data[key] = {
        "expected": expected,
        "present":  attendance_data.get(key, {}).get("present", {}),
        "closed":   False
    }
    save_data()
    await ctx.send(f"✅ Khởi tạo điểm danh ngày **{key}** với **{len(expected)}** thành viên có role `{STUDY_ROLE_NAME}`.")


@bot.command(name="closedd")
@commands.has_permissions(manage_roles=True)
async def cmd_close(ctx: commands.Context):
    """Close today's attendance (no more auto-marking from voice). Admin only."""
    key = today_key()
    if key not in attendance_data:
        await ctx.send("❌ Chưa có dữ liệu điểm danh hôm nay.")
        return
    attendance_data[key]["closed"] = True
    save_data()
    await ctx.send(f"🔒 Đã đóng điểm danh ngày **{key}**.")


@bot.command(name="summary")
@commands.has_permissions(manage_messages=True)
async def cmd_summary(ctx: commands.Context, days: int = 7):
    """Show an attendance summary for all members over the last N days. Admin only."""
    guild      = ctx.guild
    study_role = discord.utils.get(guild.roles, name=STUDY_ROLE_NAME)
    members    = [m for m in study_role.members if not m.bot] if study_role else []
    keys       = sorted(attendance_data.keys(), reverse=True)[:days]

    if not keys:
        await ctx.send("Không có dữ liệu điểm danh nào.")
        return

    lines = []
    for member in sorted(members, key=lambda m: m.display_name.lower()):
        uid = str(member.id)
        attended = sum(
            1 for k in keys
            if uid in attendance_data[k].get("present", {})
        )
        expected = sum(
            1 for k in keys
            if uid in attendance_data[k].get("expected", [])
        )
        rate = int(attended / expected * 100) if expected else 0
        bar  = "█" * (rate // 10) + "░" * (10 - rate // 10)
        lines.append(f"`{bar}` {rate:3d}% **{member.display_name}** ({attended}/{expected})")

    embed = discord.Embed(
        title=f"📈 Tổng hợp điểm danh — {days} ngày gần nhất",
        description="\n".join(lines) or "_Không có dữ liệu_",
        color=discord.Color.gold()
    )
    embed.set_footer(text=" | ".join(keys))
    await ctx.send(embed=embed)


@bot.command(name="ddhelp")
async def cmd_help(ctx: commands.Context):
    """Show all attendance bot commands."""
    embed = discord.Embed(
        title="📖 Hướng dẫn Bot Điểm Danh",
        color=discord.Color.purple()
    )
    p = COMMAND_PREFIX
    embed.add_field(
        name="👤 Lệnh cho thành viên",
        value=(
            f"`{p}myattendance [số ngày]` — Xem lịch sử điểm danh của bạn\n"
            f"`{p}ddhelp` — Hiển thị hướng dẫn này"
        ),
        inline=False
    )
    embed.add_field(
        name="🔧 Lệnh cho admin",
        value=(
            f"`{p}attendance [ngày]` — Xem điểm danh hôm nay (hoặc ngày YYYY-MM-DD)\n"
            f"`{p}mark @thành-viên` — Điểm danh thủ công\n"
            f"`{p}unmark @thành-viên` — Xoá điểm danh\n"
            f"`{p}initdd` — Khởi tạo danh sách điểm danh hôm nay\n"
            f"`{p}closedd` — Đóng điểm danh hôm nay\n"
            f"`{p}summary [số ngày]` — Tổng hợp tỉ lệ đi học"
        ),
        inline=False
    )
    embed.set_footer(text=f"Bot tự động điểm danh khi thành viên vào voice channel lúc {ANNOUNCE_HOUR:02d}:{ANNOUNCE_MINUTE:02d}.")
    await ctx.send(embed=embed)


# ── error handling ─────────────────────────────────────────────────────────

@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Bạn không có quyền dùng lệnh này.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ Không tìm thấy thành viên đó.")
    elif isinstance(error, commands.CommandNotFound):
        pass  # silently ignore unknown commands
    else:
        await ctx.send(f"⚠️ Lỗi: `{error}`")


# ─────────────────────────────────────────────────────────────────────────────
bot.run(TOKEN)
