"""Control server + dashboard for the visa watcher.

Runs the web UI on PORT (default 3025) and owns the watcher worker thread so you
can start/stop it from the browser. Auto-starts the watcher on launch (AUTOSTART).
"""
import logging
import sys
import threading

from flask import Flask, jsonify, send_file

import config
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
setInterval(()=>{ $('clock').textContent=new Date().toLocaleTimeString(); },1000);
setInterval(refresh,3000); refresh();
</script>
</body>
</html>"""


def main():
    if config.AUTOSTART:
        start_watcher()
    log.info("Dashboard on http://127.0.0.1:%d", config.PORT)
    app.run(host="127.0.0.1", port=config.PORT, threaded=True)


if __name__ == "__main__":
    main()
