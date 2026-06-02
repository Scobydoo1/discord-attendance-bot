# 📚 Discord Attendance Bot (v2)

A Discord bot that tracks study attendance with strict discipline rules: 1-hour minimum in voice, max 3 leaves, streak retention with 2-day insurance, and auto check-in via text message.

---

## ✨ Features

| Feature | Detail |
|---|---|
| 📣 Daily announcement | Pings `PING_ROLE_NAME` role at the configured hour every day |
| ✅ Auto attendance (voice) | Marks present when member joins the study voice channel |
| 💬 Auto attendance (text) | Marks present when member sends any message after announce time |
| ⏰ Late detection | Members joining after the grace period are marked late |
| ⏱️ 1-hour minimum | Members must accumulate ≥ 3600 s in the voice channel |
| 🚪 Max 3 leaves | > 3 exits from the voice channel → disqualified, DM warning sent |
| 🔥 Streak system | Consecutive-day streak with **2-day insurance** (1-day gap doesn't break it) |
| 📋 3-tier report | `/attendance` splits results into: Passed / Unqualified / Absent |
| 📊 Summary leaderboard | `/summary` shows attendance rate + current streak per member |
| ✏️ Manual override | Admins can manually mark or unmark members |
| 💬 Personal history | Each member can check their own attendance history + streak |

---

## 🚀 Setup Guide

### Step 1 — Create a Discord Application & Bot

1. Go to [https://discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **New Application**, give it a name (e.g. `AttendanceBot`)
3. Go to the **Bot** tab → click **Add Bot**
4. Under **TOKEN** → click **Reset Token** and copy it
5. Scroll down to **Privileged Gateway Intents** and enable all three:
   - ✅ Presence Intent
   - ✅ Server Members Intent
   - ✅ Message Content Intent
6. Save changes

### Step 2 — Invite the Bot to Your Server

1. Go to **OAuth2 → URL Generator**
2. Under **Scopes**, check: `bot`, `applications.commands`
3. Under **Bot Permissions**, check:
   - `Send Messages`
   - `Embed Links`
   - `Read Message History`
   - `Mention Everyone`
4. Copy the generated URL → open it in a browser → add the bot to your server

### Step 3 — Get IDs (Enable Developer Mode)

1. In Discord: **Settings → Advanced → Developer Mode** → ON
2. Right-click your **server name** → **Copy Server ID** → paste as `GUILD_ID`
3. Right-click the **announcement text channel** → **Copy Channel ID** → `ANNOUNCE_CHANNEL_ID`
4. Right-click the **study voice channel** → **Copy Channel ID** → `VOICE_CHANNEL_ID`

### Step 4 — Configure the Bot

Create a `.env` file in the project root:

```
DISCORD_TOKEN=your-bot-token-here
GUILD_ID=your-server-id
ANNOUNCE_CHANNEL_ID=your-text-channel-id
VOICE_CHANNEL_ID=your-voice-channel-id
STUDY_ROLE_NAME=study
PING_ROLE_NAME=anh em cứu vớt tuong lai
ANNOUNCE_HOUR=20
ANNOUNCE_MINUTE=0
LATE_GRACE_MINUTES=15
TIMEZONE_OFFSET=7
ATTENDANCE_FILE=data/attendance.json
```

### Step 5 — Install & Run

```bash
pip install -r requirements.txt
python bot.py
```

---

## 💬 Commands

### For all members
| Command | Description |
|---|---|
| `/myattendance [days]` | See your attendance history, streak, and longest streak |
| `/ddhelp` | Show help menu |

### For admins (requires `Manage Messages` or `Manage Roles`)
| Command | Description |
|---|---|
| `/attendance [date]` | View attendance split into 3 tiers (passed / unqualified / absent) |
| `/summary [days]` | See attendance rate + current streak for all members |
| `/mark @member` | Manually mark someone as present (auto-qualifies + updates streak) |
| `/unmark @member` | Remove someone's attendance for today |
| `/initdd` | Re-initialise today's expected list from the role |
| `/closedd` | Close today's attendance, flush voice sessions, update all streaks |

---

## 📋 Attendance Rules

| Rule | Detail |
|---|---|
| Minimum stay | Must accumulate **≥ 1 hour** (3600 s) total in the voice channel |
| Max leaves | May leave and rejoin the voice channel up to **3 times** |
| Disqualification | Leaving more than 3 times → marked absent, DM warning sent |
| Text check-in | Sending any message after announce time also marks attendance (no voice requirement for this path) |
| Streak insurance | Streak survives a **1-day gap** (skip 1 day → streak continues; skip 2+ days → reset) |

---

## 📁 Data Structure

```json
{
  "_streaks": {
    "123456789": {
      "current_streak": 5,
      "last_attended": "2026-06-01",
      "longest_streak": 12
    }
  },
  "2026-06-01": {
    "expected": ["123456789", "987654321"],
    "present": {
      "123456789": {
        "name": "Alice",
        "join_time": "20:03:12",
        "late": false,
        "manual": false,
        "source": "voice",
        "total_duration": 4200,
        "leave_count": 1,
        "disqualified": false
      }
    },
    "closed": true
  }
}
```

`_streaks` is a reserved key — date-parsing logic skips it via `is_date_key()`.

---

## ⚙️ Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `DISCORD_TOKEN` | *(required)* | Bot token from Discord Developer Portal |
| `GUILD_ID` | *(required)* | Your server ID |
| `ANNOUNCE_CHANNEL_ID` | *(required)* | Text channel for announcements |
| `VOICE_CHANNEL_ID` | *(required)* | Voice channel to monitor |
| `STUDY_ROLE_NAME` | `study` | Role name to track (case-sensitive) |
| `PING_ROLE_NAME` | `anh em cứu vớt tuong lai` | Role pinged in daily announcement |
| `ANNOUNCE_HOUR` | `20` | Hour of daily announcement (24h format) |
| `ANNOUNCE_MINUTE` | `0` | Minute of announcement |
| `LATE_GRACE_MINUTES` | `15` | Minutes after announce before marked late |
| `TIMEZONE_OFFSET` | `7` | UTC offset (Vietnam = 7) |
| `ATTENDANCE_FILE` | `data/attendance.json` | Path to data file |

---

## 🚂 Deploying on Railway

1. Push this repo to GitHub
2. Create a new Railway project → **Deploy from GitHub repo**
3. Add all environment variables in **Variables** tab (no `.env` file needed on Railway)
4. Railway auto-detects `Procfile` → runs `worker: python bot.py`
5. The bot runs 24/7 — no sleep mode like Heroku free tier
