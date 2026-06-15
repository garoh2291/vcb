"""Control server + dashboard for the visa watcher.

Runs the web UI on PORT (default 3025) and owns the watcher worker thread so you
can start/stop it from the browser. Auto-starts the watcher on launch (AUTOSTART).
"""
import logging
import sys
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, request, send_file

import config
import settings
import telegram
from watcher import WatcherState, run_watcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("server")

app = Flask(__name__)
state = WatcherState()

_lock = threading.Lock()
_thread = None
_stop_event = None


def start_watcher():
    global _thread, _stop_event
    with _lock:
        if _thread and _thread.is_alive():
            return False
        _stop_event = threading.Event()
        _thread = threading.Thread(target=run_watcher, args=(state, _stop_event), daemon=True)
        _thread.start()
        return True


def stop_watcher():
    global _thread, _stop_event
    with _lock:
        if not (_thread and _thread.is_alive()):
            return False
        _stop_event.set()
    _thread.join(timeout=30)
    return True


def _running():
    return bool(_thread and _thread.is_alive())


# ---- Scheduling ----

_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _fmt_schedule(s) -> str:
    if not s.get("enabled"):
        return "no schedule (manual)"
    days = s.get("days") or []
    dtxt = "every day" if not days else ",".join(_DAYS[d] for d in sorted(days))
    return f"{s.get('start')}–{s.get('end')} ({dtxt})"


def _parse_hhmm(t) -> int:
    h, m = str(t).split(":")
    h, m = int(h), int(m)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError("bad time")
    return h * 60 + m


def _parse_days(text):
    """'mon-fri' range, 'mon,wed,fri' list, '' => every day."""
    text = (text or "").strip().lower()
    if not text:
        return []
    if "-" in text and "," not in text:
        a, b = text.split("-")
        ia, ib = _DAYS.index(a[:3]), _DAYS.index(b[:3])
        return list(range(ia, ib + 1)) if ia <= ib else list(range(ia, 7)) + list(range(0, ib + 1))
    out = []
    for p in text.replace(" ", ",").split(","):
        if p[:3] in _DAYS:
            out.append(_DAYS.index(p[:3]))
    return sorted(set(out))


def _in_window(now, s) -> bool:
    days = s.get("days") or list(range(7))
    try:
        start, end = _parse_hhmm(s["start"]), _parse_hhmm(s["end"])
    except Exception:  # noqa: BLE001
        return False
    cur, wd = now.hour * 60 + now.minute, now.weekday()
    if start <= end:
        return wd in days and start <= cur < end
    # overnight window (e.g. 22:00–06:00)
    if wd in days and cur >= start:
        return True
    if ((wd - 1) % 7) in days and cur < end:
        return True
    return False


def scheduler_loop():
    """Every 30s, enforce the schedule window (schedule wins over manual)."""
    while True:
        try:
            s = settings.get_schedule()
            if s.get("enabled"):
                want = _in_window(datetime.now(), s)
                if want and not _running():
                    start_watcher()
                    telegram.send_message(
                        f"🗓️ Schedule: window open — watcher started. ({_fmt_schedule(s)})",
                        keyboard=True)
                elif (not want) and _running():
                    stop_watcher()
                    telegram.send_message(
                        f"🗓️ Schedule: window closed — watcher stopped. ({_fmt_schedule(s)})",
                        keyboard=True)
        except Exception as e:  # noqa: BLE001
            log.warning("scheduler error: %s", e)
        time.sleep(30)


# ---- Telegram control (long-polling) ----

def _status_text():
    d = state.snapshot()

    def ago(t):
        return "—" if not t else f"{int(d['server_time'] - t)}s ago"

    return (f"{'🟢 RUNNING' if d['running'] else '🔴 STOPPED'} · "
            f"{'online' if d['online'] else 'OFFLINE'}\n"
            f"Email: {settings.get_email()} ({settings.email_source()})\n"
            f"Schedule: {_fmt_schedule(settings.get_schedule())}\n"
            f"Checks: {d['cycles']}\n"
            f"Last check: {ago(d['last_check'])}\n"
            f"Result: {d['last_result']}")


_HELP = ("Commands:\n/start · /stop\n/run (check now)\n/shots (check + screenshots)\n"
         "/status · /whoami\n/setemail you@x.com\n/setpassword secret\n"
         "/schedule 09:00 21:00 mon-fri · /schedule off")


def _handle_schedule_command(arg):
    a = arg.strip().lower()
    if not a:
        telegram.send_message(
            f"🗓️ Current schedule: {_fmt_schedule(settings.get_schedule())}\n"
            "Set: /schedule 09:00 21:00 mon-fri\nRemove: /schedule off", keyboard=True)
        return
    if a in ("off", "remove", "none", "clear", "disable"):
        settings.clear_schedule()
        telegram.send_message("🗓️ Schedule removed (manual mode).", keyboard=True)
        return
    parts = a.split()
    if len(parts) < 2:
        telegram.send_message("Usage: /schedule 09:00 21:00 [mon-fri]", keyboard=True)
        return
    start, end = parts[0], parts[1]
    days = _parse_days(parts[2]) if len(parts) > 2 else []
    try:
        _parse_hhmm(start)
        _parse_hhmm(end)
    except Exception:  # noqa: BLE001
        telegram.send_message("Times must be HH:MM, e.g. /schedule 09:00 21:00", keyboard=True)
        return
    settings.set_schedule(start, end, days)
    telegram.send_message(f"🗓️ Schedule set: {_fmt_schedule(settings.get_schedule())}",
                          keyboard=True)


def _handle_command(text):
    raw = text.strip()
    t = raw.lower()
    if t in ("/start", "▶ start", "start"):
        ok = start_watcher()
        telegram.send_message("▶ Watcher started." if ok else "Already running.", keyboard=True)
    elif t in ("/stop", "■ stop", "stop"):
        ok = stop_watcher()
        telegram.send_message("■ Watcher stopped." if ok else "Already stopped.", keyboard=True)
    elif t in ("/run", "🔄 run now", "run", "run now"):
        if _running():
            state.request_check(False)
            telegram.send_message("🔄 Checking now…", keyboard=True)
        else:
            telegram.send_message("Watcher is stopped. Send /start first.", keyboard=True)
    elif t in ("/shots", "📸 run + shots", "shots", "run + shots"):
        if _running():
            state.request_check(True)
            telegram.send_message("📸 Checking now + sending screenshots…", keyboard=True)
        else:
            telegram.send_message("Watcher is stopped. Send /start first.", keyboard=True)
    elif t in ("/status", "📊 status", "status"):
        telegram.send_message(_status_text(), keyboard=True)
    elif t in ("/whoami", "whoami"):
        telegram.send_message(
            f"📧 Email: {settings.get_email()} (source: {settings.email_source()})\n"
            f"Password: {'set' if settings.password_set() else 'NOT set'}", keyboard=True)
    else:
        parts = raw.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd == "/setemail":
            if not arg:
                telegram.send_message("Usage: /setemail you@example.com", keyboard=True)
            else:
                settings.set_credentials(email=arg)
                if _running():
                    state.request_relogin()
                telegram.send_message(f"📧 Email updated to {settings.get_email()}.", keyboard=True)
        elif cmd == "/setpassword":
            if not arg:
                telegram.send_message("Usage: /setpassword yourpassword", keyboard=True)
            else:
                settings.set_credentials(password=arg)
                if _running():
                    state.request_relogin()
                telegram.send_message(
                    "🔑 Password updated. (You can delete your message above for safety.)",
                    keyboard=True)
        elif cmd == "/schedule":
            _handle_schedule_command(arg)
        else:
            telegram.send_message(_HELP, keyboard=True)


def telegram_poller():
    """Long-poll Telegram for commands. Only the configured chat id is honoured."""
    offset = None
    # Drain any backlog so we don't replay stale commands on boot.
    try:
        old = telegram.get_updates(timeout=0)
        if old:
            offset = old[-1]["update_id"] + 1
    except Exception:  # noqa: BLE001
        pass
    while True:
        try:
            for u in telegram.get_updates(offset=offset, timeout=50):
                offset = u["update_id"] + 1
                msg = u.get("message") or u.get("edited_message")
                if not msg:
                    continue
                if str(msg.get("chat", {}).get("id")) != str(config.TELEGRAM_CHAT_ID):
                    continue  # ignore everyone but you
                text = msg.get("text", "")
                if text:
                    _handle_command(text)
        except Exception as e:  # noqa: BLE001
            log.warning("telegram poller error: %s", e)
            time.sleep(5)


@app.get("/api/status")
def api_status():
    return jsonify(state.snapshot())


@app.post("/api/start")
def api_start():
    ok = start_watcher()
    return jsonify({"ok": ok, "msg": "started" if ok else "already running"})


@app.post("/api/stop")
def api_stop():
    ok = stop_watcher()
    return jsonify({"ok": ok, "msg": "stopped" if ok else "not running"})


@app.get("/api/config")
def api_config():
    return jsonify({
        "email": settings.get_email(),
        "email_source": settings.email_source(),
        "password_set": settings.password_set(),
        "schedule": settings.get_schedule(),
    })


@app.post("/api/credentials")
def api_credentials():
    data = request.get_json(silent=True) or request.form
    email = (data.get("email") or "").strip()
    password = (data.get("password") or "").strip()
    if not email and not password:
        return jsonify({"ok": False, "msg": "nothing to update"}), 400
    settings.set_credentials(email, password)
    if _running():
        state.request_relogin()
    changed = " & ".join([w for w, v in (("email", email), ("password", password)) if v])
    telegram.send_message(
        f"🔧 Config changed via UI: {changed} updated.\nEmail now: {settings.get_email()}",
        keyboard=True)
    return jsonify({"ok": True, "email": settings.get_email()})


@app.post("/api/schedule")
def api_schedule():
    data = request.get_json(silent=True) or request.form
    start = (data.get("start") or "").strip()
    end = (data.get("end") or "").strip()
    days = data.get("days") or []
    days = _parse_days(days) if isinstance(days, str) else [int(d) for d in days]
    try:
        _parse_hhmm(start)
        _parse_hhmm(end)
    except Exception:  # noqa: BLE001
        return jsonify({"ok": False, "msg": "times must be HH:MM"}), 400
    settings.set_schedule(start, end, days)
    s = settings.get_schedule()
    telegram.send_message(f"🗓️ Config changed via UI: schedule set {_fmt_schedule(s)}.",
                          keyboard=True)
    return jsonify({"ok": True, "schedule": s})


@app.post("/api/schedule/remove")
def api_schedule_remove():
    settings.clear_schedule()
    telegram.send_message("🗓️ Config changed via UI: schedule removed (manual mode).",
                          keyboard=True)
    return jsonify({"ok": True})


@app.get("/screenshot.png")
def screenshot():
    snap = state.snapshot()
    if snap["has_screenshot"] and state.last_screenshot:
        return send_file(state.last_screenshot, mimetype="image/png")
    return ("no screenshot yet", 404)


@app.get("/month/<int:idx>.png")
def month_shot(idx):
    shots = list(state.month_shots)
    if 0 <= idx < len(shots) and shots[idx].get("path"):
        return send_file(shots[idx]["path"], mimetype="image/png")
    return ("no screenshot", 404)


@app.get("/")
def index():
    return DASHBOARD


DASHBOARD = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VISA WATCH · control</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@600;800&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#0a0b0d; --panel:#101317; --line:#1c2127; --ink:#e8ebe6;
    --dim:#717a82; --amber:#ffb000; --green:#39d98a; --red:#ff4d4d; --blue:#48b0ff;
  }
  *{box-sizing:border-box}
  html,body{margin:0}
  body{
    background:
      radial-gradient(900px 500px at 80% -10%, rgba(255,176,0,.06), transparent 60%),
      repeating-linear-gradient(0deg, transparent 0 38px, rgba(255,255,255,.012) 38px 39px),
      var(--bg);
    color:var(--ink); font-family:"IBM Plex Mono",monospace;
    min-height:100vh; padding:28px clamp(16px,4vw,56px);
  }
  .wrap{max-width:1100px;margin:0 auto}
  header{display:flex;align-items:baseline;justify-content:space-between;gap:16px;flex-wrap:wrap;
    border-bottom:1px solid var(--line);padding-bottom:18px}
  h1{font-family:"Syne",sans-serif;font-weight:800;font-size:clamp(28px,5vw,46px);
    letter-spacing:-.02em;margin:0;line-height:.95}
  h1 small{display:block;font-family:"IBM Plex Mono";font-weight:500;font-size:12px;
    letter-spacing:.32em;color:var(--amber);margin-bottom:8px;text-transform:uppercase}
  .clock{color:var(--dim);font-size:13px;letter-spacing:.1em}

  .statusbar{display:flex;align-items:center;gap:18px;flex-wrap:wrap;margin:26px 0}
  .pill{display:inline-flex;align-items:center;gap:12px;border:1px solid var(--line);
    background:var(--panel);padding:14px 22px;border-radius:2px}
  .dot{width:14px;height:14px;border-radius:50%;background:var(--dim);
    box-shadow:0 0 0 0 rgba(0,0,0,0)}
  .dot.on{background:var(--green);animation:pulse 1.8s infinite}
  .dot.off{background:var(--red)}
  .dot.warn{background:var(--amber)}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(57,217,138,.5)}70%{box-shadow:0 0 0 12px rgba(57,217,138,0)}100%{box-shadow:0 0 0 0 rgba(57,217,138,0)}}
  .pill b{font-family:"Syne";font-weight:800;font-size:18px;letter-spacing:.02em}
  .pill span{color:var(--dim);font-size:12px}

  .btns{margin-left:auto;display:flex;gap:12px}
  button{font-family:"IBM Plex Mono";font-weight:600;font-size:14px;letter-spacing:.08em;
    text-transform:uppercase;cursor:pointer;border:1px solid var(--line);background:#15191e;
    color:var(--ink);padding:14px 26px;border-radius:2px;transition:.15s}
  button:hover{border-color:var(--amber);color:var(--amber)}
  button.go{border-color:var(--green);color:var(--green)}
  button.go:hover{background:var(--green);color:#04140c}
  button.halt{border-color:var(--red);color:var(--red)}
  button.halt:hover{background:var(--red);color:#1a0303}
  button:disabled{opacity:.3;cursor:not-allowed;border-color:var(--line);color:var(--dim)}

  .grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:8px 0 26px}
  .tile{border:1px solid var(--line);background:var(--panel);padding:16px 18px;border-radius:2px}
  .tile .k{color:var(--dim);font-size:11px;letter-spacing:.18em;text-transform:uppercase}
  .tile .v{font-family:"Syne";font-weight:800;font-size:26px;margin-top:8px}

  .cols{display:grid;grid-template-columns:1.2fr 1fr;gap:18px}
  @media(max-width:820px){.cols{grid-template-columns:1fr}.grid{grid-template-columns:repeat(2,1fr)}.btns{margin-left:0;width:100%}.btns button{flex:1}}
  .card{border:1px solid var(--line);background:var(--panel);border-radius:2px;overflow:hidden}
  .card h2{font-family:"IBM Plex Mono";font-weight:600;font-size:12px;letter-spacing:.2em;
    text-transform:uppercase;color:var(--amber);margin:0;padding:14px 18px;border-bottom:1px solid var(--line)}
  .feed{height:360px;overflow:auto;padding:10px 0;font-size:13px}
  .feed .row{display:flex;gap:12px;padding:5px 18px}
  .feed .row:hover{background:#0d1013}
  .feed time{color:var(--dim);white-space:nowrap}
  .feed .msg{color:var(--ink)}
  .feed .error .msg{color:var(--red)} .feed .warning .msg{color:var(--amber)}
  .shot{display:block;width:100%;background:#000;min-height:200px}
  .shotwrap{padding:0}
  .result{padding:14px 18px;border-top:1px solid var(--line);color:var(--dim);font-size:13px;word-break:break-word}
  .found{color:var(--green);font-weight:600}
  .months{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;padding:16px}
  @media(max-width:820px){.months{grid-template-columns:1fr}}
  .month{border:1px solid var(--line);border-radius:2px;overflow:hidden;background:#0d1013}
  .month .lbl{font-family:"Syne";font-weight:800;font-size:16px;padding:10px 12px;
    border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center}
  .month .tag{font-family:"IBM Plex Mono";font-size:10px;letter-spacing:.12em;padding:3px 8px;
    border-radius:2px;text-transform:uppercase}
  .month .tag.none{color:var(--dim);border:1px solid var(--line)}
  .month .tag.slots{color:#04140c;background:var(--green)}
  .month img{display:block;width:100%;background:#000;cursor:zoom-in}
  .empty{color:var(--dim);font-size:13px;padding:20px}
  .cfg{padding:16px 18px;display:flex;flex-direction:column;gap:8px}
  .cfg .cur{font-size:13px;color:var(--dim);margin-bottom:6px}
  .cfg .cur b{color:var(--ink);font-family:"Syne";font-weight:800}
  .cfg .src{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--amber);
    border:1px solid var(--line);border-radius:2px;padding:2px 6px;margin-left:6px}
  .cfg label{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--dim);margin-top:6px}
  .cfg input{background:#0d1013;border:1px solid var(--line);color:var(--ink);
    font-family:"IBM Plex Mono";font-size:14px;padding:10px 12px;border-radius:2px;width:100%}
  .cfg input:focus{outline:none;border-color:var(--amber)}
  .row2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  .days{display:flex;flex-wrap:wrap;gap:6px}
  .days label{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--line);
    padding:6px 9px;border-radius:2px;cursor:pointer;color:var(--ink);text-transform:uppercase;margin:0}
  .days input{width:auto}
  .btnrow{display:flex;gap:10px;margin-top:6px}
  button.save{border-color:var(--green);color:var(--green);margin-top:10px}
  button.save:hover{background:var(--green);color:#04140c}
  button.del{border-color:var(--red);color:var(--red);margin-top:10px}
  button.del:hover{background:var(--red);color:#1a0303}
  .hint{font-size:12px;color:var(--dim);min-height:14px}
  .hint.ok{color:var(--green)} .hint.err{color:var(--red)}
  footer{color:var(--dim);font-size:11px;letter-spacing:.1em;margin-top:26px;text-align:center}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1><small>TLScontact · Yerevan</small>VISA&nbsp;WATCH</h1>
    <div class="clock" id="clock">--:--:--</div>
  </header>

  <div class="statusbar">
    <div class="pill"><span class="dot" id="runDot"></span><div><b id="runText">…</b><br><span>watcher</span></div></div>
    <div class="pill"><span class="dot" id="netDot"></span><div><b id="netText">…</b><br><span>internet</span></div></div>
    <div class="btns">
      <button class="go" id="startBtn" onclick="act('start')">▶ start</button>
      <button class="halt" id="stopBtn" onclick="act('stop')">■ stop</button>
    </div>
  </div>

  <div class="grid">
    <div class="tile"><div class="k">Checks done</div><div class="v" id="cycles">0</div></div>
    <div class="tile"><div class="k">Last check</div><div class="v" id="lastCheck">—</div></div>
    <div class="tile"><div class="k">Last alert</div><div class="v" id="lastAlert">—</div></div>
    <div class="tile"><div class="k">Uptime</div><div class="v" id="uptime">—</div></div>
  </div>

  <div class="cols">
    <div class="card">
      <h2>Live log</h2>
      <div class="feed" id="feed"></div>
    </div>
    <div class="card">
      <h2>Status</h2>
      <div class="result" id="result" style="border-top:none">—</div>
    </div>
  </div>

  <div class="cols" style="margin-top:18px">
    <div class="card">
      <h2>Credentials</h2>
      <div class="cfg">
        <div class="cur">In use: <b id="curEmail">—</b> <span class="src" id="emailSrc"></span></div>
        <label>Email</label>
        <input id="inEmail" type="email" placeholder="you@example.com" autocomplete="off">
        <label>Password</label>
        <input id="inPass" type="password" placeholder="leave blank to keep current" autocomplete="new-password">
        <button class="save" onclick="saveCreds()">Save credentials</button>
        <div class="hint" id="credMsg"></div>
      </div>
    </div>
    <div class="card">
      <h2>Schedule</h2>
      <div class="cfg">
        <div class="cur">Now: <b id="curSched">—</b></div>
        <div class="row2"><div><label>Start</label><input id="inStart" type="time" value="09:00"></div>
          <div><label>End</label><input id="inEnd" type="time" value="21:00"></div></div>
        <label>Days (blank = every day)</label>
        <div class="days" id="dayBoxes"></div>
        <div class="btnrow">
          <button class="save" onclick="saveSchedule()">Save schedule</button>
          <button class="del" onclick="removeSchedule()">Remove</button>
        </div>
        <div class="hint" id="schedMsg"></div>
      </div>
    </div>
  </div>

  <div class="card" style="margin-top:18px">
    <h2>Last screenshots — 3 months</h2>
    <div class="months" id="months">
      <div class="empty">No screenshots yet — first check in progress…</div>
    </div>
  </div>

  <footer>POLLING /api/status · local control panel · keep this Mac awake</footer>
</div>

<script>
const $=id=>document.getElementById(id);
function ago(t,now){ if(!t) return "—"; let s=Math.max(0,Math.round(now-t));
  if(s<60) return s+"s ago"; if(s<3600) return Math.round(s/60)+"m ago"; return Math.round(s/3600)+"h ago";}
function dur(t,now){ if(!t) return "—"; let s=Math.round(now-t);
  let h=Math.floor(s/3600),m=Math.floor(s%3600/60); return h+"h "+m+"m";}
async function act(which){
  $('startBtn').disabled=$('stopBtn').disabled=true;
  try{ await fetch('/api/'+which,{method:'POST'}); }catch(e){}
  setTimeout(refresh,400);
}
async function refresh(){
  let d; try{ d=await (await fetch('/api/status')).json(); }catch(e){ return; }
  const now=d.server_time;
  $('runDot').className='dot '+(d.running?'on':'off');
  $('runText').textContent=d.running?'RUNNING':'STOPPED';
  $('netDot').className='dot '+(d.online?'on':'warn');
  $('netText').textContent=d.online?'ONLINE':'OFFLINE';
  $('startBtn').disabled=d.running; $('stopBtn').disabled=!d.running;
  $('cycles').textContent=d.cycles;
  $('lastCheck').textContent=ago(d.last_check,now);
  $('lastAlert').textContent=ago(d.last_alert,now);
  $('uptime').textContent=d.running?dur(d.started_at,now):'—';
  const r=$('result');
  r.innerHTML=(d.found?'<span class="found">★ SLOT DETECTED</span> · ':'')+(d.last_result||'—');
  $('feed').innerHTML=(d.log||[]).map(e=>
    `<div class="row ${e.level}"><time>${new Date(e.t*1000).toLocaleTimeString()}</time><span class="msg">${e.msg.replace(/</g,'&lt;')}</span></div>`
  ).join('');
  const months=d.months||[], mc=$('months'), tb=Math.floor(now);
  if(months.length){
    mc.innerHTML=months.map(m=>
      `<div class="month"><div class="lbl">${m.label}<span class="tag ${m.has_slots?'slots':'none'}">${m.has_slots?'SLOTS ✶':'none'}</span></div>`+
      `<a href="/month/${m.index}.png?t=${tb}" target="_blank"><img src="/month/${m.index}.png?t=${tb}" alt="${m.label}"></a></div>`
    ).join('');
  } else {
    mc.innerHTML='<div class="empty">No screenshots yet — first check in progress…</div>';
  }
}
// ---- config card ----
const DAYS=["mon","tue","wed","thu","fri","sat","sun"];
$('dayBoxes').innerHTML=DAYS.map((d,i)=>`<label><input type="checkbox" value="${i}">${d}</label>`).join('');
function flash(id,msg,ok){ const e=$(id); e.textContent=msg; e.className='hint '+(ok?'ok':'err'); setTimeout(()=>{e.textContent='';e.className='hint';},5000); }
async function loadConfig(){
  let c; try{ c=await (await fetch('/api/config')).json(); }catch(e){ return; }
  $('curEmail').textContent=c.email||'—';
  $('emailSrc').textContent=c.email_source||'';
  const s=c.schedule||{};
  $('curSched').textContent = s.enabled ? `${s.start}–${s.end} (${(s.days&&s.days.length)?s.days.map(d=>DAYS[d]).join(','):'every day'})` : 'no schedule (manual)';
  if(s.enabled){ if(s.start)$('inStart').value=s.start; if(s.end)$('inEnd').value=s.end;
    document.querySelectorAll('#dayBoxes input').forEach(b=>b.checked=(s.days||[]).includes(+b.value)); }
}
async function saveCreds(){
  const email=$('inEmail').value.trim(), password=$('inPass').value;
  if(!email && !password){ flash('credMsg','Enter an email or password',false); return; }
  try{ const r=await (await fetch('/api/credentials',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,password})})).json();
    if(r.ok){ $('inPass').value=''; flash('credMsg','Saved · Telegram notified',true); loadConfig(); }
    else flash('credMsg',r.msg||'failed',false);
  }catch(e){ flash('credMsg','request failed',false); }
}
async function saveSchedule(){
  const start=$('inStart').value, end=$('inEnd').value;
  const days=[...document.querySelectorAll('#dayBoxes input:checked')].map(b=>+b.value);
  try{ const r=await (await fetch('/api/schedule',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({start,end,days})})).json();
    if(r.ok){ flash('schedMsg','Schedule saved · Telegram notified',true); loadConfig(); }
    else flash('schedMsg',r.msg||'failed',false);
  }catch(e){ flash('schedMsg','request failed',false); }
}
async function removeSchedule(){
  try{ await fetch('/api/schedule/remove',{method:'POST'}); flash('schedMsg','Schedule removed',true); loadConfig(); }
  catch(e){ flash('schedMsg','request failed',false); }
}
setInterval(()=>{ $('clock').textContent=new Date().toLocaleTimeString(); },1000);
setInterval(refresh,3000); refresh();
loadConfig(); setInterval(loadConfig,15000);
</script>
</body>
</html>"""


def main():
    # Register the "/" command menu and start the Telegram control poller.
    telegram.set_commands()
    threading.Thread(target=telegram_poller, daemon=True).start()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    telegram.send_message("🤖 Visa watcher control ready. Tap a button or use /help.",
                          keyboard=True)
    # When a schedule is enabled, the scheduler decides when to run. Otherwise honour AUTOSTART.
    if config.AUTOSTART and not settings.get_schedule().get("enabled"):
        start_watcher()
    log.info("Dashboard on http://127.0.0.1:%d", config.PORT)
    app.run(host="127.0.0.1", port=config.PORT, threaded=True)


if __name__ == "__main__":
    main()
