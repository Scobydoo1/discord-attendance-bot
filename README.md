# 📚 Discord Attendance Bot

A Discord bot that automatically tracks attendance for members with the **study** role. It announces study time at 8 PM and marks members present when they join the designated voice channel.

---

## ✨ Features

| Feature | Detail |
|---|---|
| 📣 Daily announcement | Pings `@AE-Cứu vớt tương lai` role at 8 PM every day |
| ✅ Auto attendance | Marks members present when they join the voice channel |
| ⏰ Late detection | Members joining after the grace period are marked late |
| 📋 Attendance report | Admin command to view who's present / absent |
| 📊 Summary report | Attendance rate % for all members over N days |
| ✏️ Manual override | Admins can manually mark or unmark members |
| 💬 Personal history | Each member can check their own attendance history |

---

## 🚀 Setup Guide

### Step 1 — Create a Discord Application & Bot

1. Go to [https://discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **New Application**, give it a name (e.g. `AttendanceBot`)
3. Go to the **Bot** tab → click **Add Bot**
4. Under **TOKEN** → click **Reset Token** and copy it (you'll need it later)
5. Scroll down to **Privileged Gateway Intents** and enable all three:
   - ✅ Presence Intent
   - ✅ Server Members Intent
   - ✅ Message Content Intent
6. Save changes

### Step 2 — Invite the Bot to Your Server

1. Go to **OAuth2 → URL Generator**
2. Under **Scopes**, check: `bot`
3. Under **Bot Permissions**, check:
   - `Send Messages`
   - `Embed Links`
   - `Read Message History`
   - `Mention Everyone` (for role pings)
4. Copy the generated URL → open it in a browser → add the bot to your server

### Step 3 — Get IDs (Enable Developer Mode)

1. In Discord: **Settings → Advanced → Developer Mode** → ON
2. Right-click your **server name** → **Copy Server ID** → paste as `GUILD_ID`
3. Right-click the **announcement text channel** → **Copy Channel ID** → `ANNOUNCE_CHANNEL_ID`
4. Right-click the **study voice channel** → **Copy Channel ID** → `VOICE_CHANNEL_ID`

### Step 4 — Configure the Bot

```bash
# Copy the example config file
cp .env.example .env

# Edit .env with your values
nano .env   # or use any text editor
```

Fill in these required values in `.env`:

```
DISCORD_TOKEN=your-bot-token-here
GUILD_ID=your-server-id
ANNOUNCE_CHANNEL_ID=your-text-channel-id
VOICE_CHANNEL_ID=your-voice-channel-id
```

### Step 5 — Install & Run

```bash
# Install Python dependencies
pip install -r requirements.txt

# Start the bot
python bot.py
```

You should see: `✅ AttendanceBot#1234 is online!`

---

## 💬 Commands

### For all members
| Command | Description |
|---|---|
| `!myattendance` | See your attendance history (last 7 days) |
| `!myattendance 30` | See history for last 30 days |
| `!ddhelp` | Show help menu |

### For admins (requires `Manage Messages` or `Manage Roles`)
| Command | Description |
|---|---|
| `!attendance` | View today's attendance report |
| `!attendance 2024-12-25` | View attendance for a specific date |
| `!summary` | See attendance rate % for all members (last 7 days) |
| `!summary 30` | Summary for last 30 days |
| `!mark @member` | Manually mark someone as present |
| `!unmark @member` | Remove someone's attendance for today |
| `!initdd` | Re-initialise today's expected list from the role |
| `!closedd` | Close today's attendance (no more auto-marking) |

---

## 📁 How Data is Stored

Attendance is saved to `attendance.json` automatically:

```json
{
  "2024-12-25": {
    "expected": ["111222333", "444555666"],
    "present": {
      "111222333": {
        "name": "Alice",
        "join_time": "20:03:12",
        "late": false,
        "manual": false
      }
    },
    "closed": false
  }
}
```

---

## 🔄 How to Keep the Bot Running 24/7

### Option A — Screen (Linux/VPS)
```bash
screen -S attendancebot
python bot.py
# Press Ctrl+A then D to detach
```

### Option B — PM2 (Node.js process manager)
```bash
npm install -g pm2
pm2 start bot.py --interpreter python3 --name attendancebot
pm2 save
pm2 startup
```

### Option C — systemd service (Linux)
Create `/etc/systemd/system/attendancebot.service`:
```ini
[Unit]
Description=Discord Attendance Bot
After=network.target

[Service]
WorkingDirectory=/path/to/your/bot
ExecStart=/usr/bin/python3 bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```
Then: `sudo systemctl enable attendancebot && sudo systemctl start attendancebot`

---

## ⚙️ Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `DISCORD_TOKEN` | *(required)* | Bot token from Discord Developer Portal |
| `GUILD_ID` | *(required)* | Your server ID |
| `ANNOUNCE_CHANNEL_ID` | *(required)* | Text channel for announcements |
| `VOICE_CHANNEL_ID` | *(required)* | Voice channel to monitor |
| `STUDY_ROLE_NAME` | `study` | Role name to track (case-sensitive) |
| `ANNOUNCE_HOUR` | `20` | Hour of daily announcement (24h format) |
| `ANNOUNCE_MINUTE` | `0` | Minute of announcement |
| `LATE_GRACE_MINUTES` | `15` | Minutes after announce time before marked late |
| `TIMEZONE_OFFSET` | `7` | UTC offset (Vietnam = 7) |
| `ATTENDANCE_FILE` | `attendance.json` | Path to data file |
