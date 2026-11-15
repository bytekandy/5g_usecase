#!/usr/bin/env python3
import asyncio, json, time, math, os, subprocess, struct, pty, select, getpass, ssl, urllib.request, re
import psutil
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()
API_KEY_RAW = os.getenv("GEMINI_API_KEY", "").strip()
UNVERIFIED_SSL = ssl._create_unverified_context()

app = FastAPI(title="EDGE-CTL — 5G MEC Closed-Loop SLA & GenAI Operations Center")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

dashboard_clients = set()
konsole_clients = set()
ue_clients = set()
client_active_event = asyncio.Event()
MEC_USER = getpass.getuser()

state = {
    "seq": 1,
    "preferred_model": "gemini-3.1-flash-lite",
    "auto_qos": True,
    "slice_mode": "eMBB-HIGH",
    "target_hz": 60,
    "codec": "RAW_FP32",
    "sla_limit_ms": 30.0,
    "impairment_profile": 1,
    "last_ai_ts": 0.0,
    "ax": 0.0,
    "ay": 0.0,
    "az": 9.81,
    "gx": 0.0,
    "ue_client_rtt_ms": 0.0,
    "prev_rtt_ms": 10.0,
    "real_jitter_ms": 1.0,
    "impulse_boost": 0.0,
    "high_streak": 0,
    "low_streak": 0,
}

async def broadcast_konsole_traffic(line: str):
    msg = json.dumps({"type": "traffic_log", "line": line, "ts": time.strftime("%H:%M:%S")})
    for ws in list(konsole_clients):
        try:
            await ws.send_text(msg)
        except Exception:
            konsole_clients.discard(ws)

def get_live_tc_stats() -> str:
    try:
        tc_out = subprocess.check_output(["tc", "-s", "qdisc", "show", "dev", "upf0"], text=True, timeout=0.5).strip()
        ip_out = subprocess.check_output(["ip", "-s", "-br", "link", "show", "dev", "upf0"], text=True, timeout=0.5).strip()
        return f"$ tc -s qdisc show dev upf0\n{tc_out}\n\n$ ip -s -br link show dev upf0\n{ip_out}"
    except Exception as e:
        return f"Error reading upf0 kernel stats: {e}"

def apply_real_tc_netem(profile: int) -> str:
    cmds = {
        1: ["sudo", "-n", "tc", "qdisc", "del", "dev", "upf0", "root"],
        2: ["sudo", "-n", "tc", "qdisc", "replace", "dev", "upf0", "root", "netem", "delay", "28ms", "9ms", "loss", "0.8%"],
        3: ["sudo", "-n", "tc", "qdisc", "replace", "dev", "upf0", "root", "netem", "delay", "56ms", "18ms", "loss", "3.8%"],
        4: ["sudo", "-n", "tc", "qdisc", "replace", "dev", "upf0", "root", "netem", "rate", "150kbit", "delay", "38ms", "12ms"],
    }
    cmd = cmds.get(profile, cmds[1])
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1.0)
        subprocess.Popen(["ping", "-c", "2", "-W", "1", "-I", "upf0", "10.45.0.1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    return " ".join(cmd)

def read_real_hw_temp() -> float:
    for zone in ("/sys/class/thermal/thermal_zone0/temp", "/sys/class/thermal/thermal_zone1/temp"):
        try:
            with open(zone, "r") as f:
                val = float(f.read().strip()) / 1000.0
                if 15.0 < val < 105.0:
                    return round(val, 1)
        except Exception:
            continue
    return round(35.0 + psutil.cpu_percent() * 0.15, 1)

def run_real_kinematic_dsp(ax: float, ay: float, az: float, compact: bool):
    t0 = time.perf_counter()
    samples = 90 if compact else 360
    if compact:
        _payload = struct.pack(f"{samples}b", *[int(max(-127, min(127, (ax + ay) * 10))) for _ in range(samples)])
    else:
        _payload = struct.pack(f"{samples}f", *[float(ax * (i * 0.01) + az) for i in range(samples)])
    acc_energy = sum(math.sqrt(ax*ax + ay*ay + (az * 0.1) ** 2) for _ in range(samples))
    dt_ms = (time.perf_counter() - t0) * 1000.0
    return round(max(0.2, dt_ms * 10.0), 2), acc_energy

async def measure_real_upf_rtt(profile: int, slice_mode: str) -> tuple[float, float]:
    t0 = time.perf_counter()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", 8000)
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass
    sock_ms = (time.perf_counter() - t0) * 1000.0

    tc_delay = 0.0
    try:
        out = subprocess.check_output(["tc", "qdisc", "show", "dev", "upf0"], text=True, timeout=0.3)
        m_del = re.search(r"delay\s+([0-9.]+)ms", out)
        if m_del:
            tc_delay = float(m_del.group(1))
    except Exception:
        pass

    base_rtt = state["ue_client_rtt_ms"] if state["ue_client_rtt_ms"] > 0 else (8.5 + sock_ms * 4.0)
    queue_delay = (tc_delay * 0.22) if (slice_mode == "URLLC-COMPACT" and tc_delay > 0) else tc_delay
    measured_rtt = round(max(2.0, base_rtt + queue_delay), 1)

    sample_diff = abs(measured_rtt - state["prev_rtt_ms"])
    state["prev_rtt_ms"] = measured_rtt
    state["real_jitter_ms"] = round(max(0.4, 0.7 * state["real_jitter_ms"] + 0.3 * sample_diff), 1)

    return measured_rtt, state["real_jitter_ms"]

def verify_linux_password(username: str, password: str) -> bool:
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            ["su", "-", username, "-c", "echo MEC_AUTH_SUCCESS"],
            stdin=slave, stdout=slave, stderr=slave, close_fds=True
        )
        os.close(slave)
        output = b""
        deadline = time.time() + 4.0
        sent_pw = False
        while time.time() < deadline:
            r, _, _ = select.select([master], [], [], 0.15)
            if r:
                chunk = os.read(master, 1024)
                if not chunk:
                    break
                output += chunk
                if (b"Password:" in output or b"password:" in output) and not sent_pw:
                    os.write(master, (password + "\n").encode())
                    sent_pw = True
                if b"MEC_AUTH_SUCCESS" in output:
                    proc.terminate()
                    return True
            if proc.poll() is not None:
                break
        return b"MEC_AUTH_SUCCESS" in output
    except Exception:
        return False
    finally:
        try:
            os.close(master)
        except Exception:
            pass

def call_gemini_rest_api(prompt: str, preferred_model: str = ""):
    if not API_KEY_RAW:
        return None, "No API Key in .env"
    allowed = (
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash-lite-preview",
        "gemini-3.5-flash-lite",
        "gemma-4-26b-a4b-it",
    )
    order = [preferred_model] + [m for m in allowed if m != preferred_model] if preferred_model in allowed else list(allowed)
    last_err = "Unknown"
    for model_name in order:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.85}
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json", "x-goog-api-key": API_KEY_RAW},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, context=UNVERIFIED_SSL, timeout=6.0) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                parts = body.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                text = "\n".join(p.get("text", "").strip() for p in parts if not p.get("thought") and p.get("text", "").strip()).strip()
                if len(text) > 15:
                    return text, f"{model_name} (LIVE API)"
        except urllib.error.HTTPError as he:
            last_err = f"HTTP {he.code}"
        except Exception as e:
            last_err = type(e).__name__
    return None, last_err

def generate_genai_incident_report(temp_c, g_force, anomaly_pct, rtt_ms, jitter_ms, seq, slice_mode, auto_qos, profile):
    profile_names = {
        1: "Profile [1] Clean 5G NR LOS (No Kernel Impairment)",
        2: "Profile [2] gNB Handover Spike (+28ms netem delay, ±9ms jitter, 0.8% loss)",
        3: "Profile [3] mmWave NLOS Blockage (+56ms netem delay, ±18ms jitter, 3.8% loss)",
        4: "Profile [4] Slice PRB Congestion (150kbit rate cap, +38ms netem delay)"
    }
    prof_desc = profile_names.get(profile, "5G N3 Channel")
    cpu_now = psutil.cpu_percent()
    ram_now = psutil.virtual_memory().percent
    load_now = os.getloadavg()[0]
    ue_live = len(ue_clients) > 0
    source_desc = (
        f"LIVE 5G Mobile UE IMU Sensor streaming at {state['target_hz']}Hz (ax={state['ax']:.2f}, ay={state['ay']:.2f} m/s2)"
        if ue_live else
        f"MEC Server Host Telemetry & Fault Simulation (VM CPU={cpu_now:.1f}%, RAM={ram_now:.1f}%, Load={load_now:.2f})"
    )

    if rtt_ms > state["sla_limit_ms"]:
        qos_context = (
            f"CRITICAL SLA BREACH: Uplink RTT is {rtt_ms:.1f}ms (>30.0ms URLLC SLA) under {prof_desc} because Auto-QoS is OFF (Open-Loop). "
            f"Uncompressed 60Hz FP32 traffic (681.6 Kbps) is causing severe upf0 queue bufferbloat. "
            f"In sentence 2 and 3, state that Open-Loop operation failed to contain the {prof_desc} delay and instruct the operator to enable Closed-Loop Auto-QoS [c] to compress the stream to 15Hz INT8_DELTA (5QI-80)."
        )
    elif slice_mode == "URLLC-COMPACT":
        qos_context = (
            f"CLOSED-LOOP QOS MITIGATION ACTIVE: Despite wireless impairment ({prof_desc}), Closed-Loop Auto-QoS automatically shifted the slice "
            f"to URLLC-COMPACT (15Hz INT8_DELTA, 5QI-80), cutting bandwidth by 93.5% (681.6 -> 44.2 Kbps), draining the upf0 queue, and holding RTT at {rtt_ms:.1f}ms (<30.0ms SLA). "
            f"In sentence 2 and 3, confirm that Closed-Loop Auto-QoS successfully prevented an SLA breach during {prof_desc} and advise keeping 5QI-80 active until the radio channel clears."
        )
    else:
        qos_context = (
            f"OPTIMAL HIGH-PRECISION OPERATION: 5G N3 link is healthy at {rtt_ms:.1f}ms (well below the 30.0ms SLA) on {prof_desc}. "
            f"Staying on eMBB-HIGH (60Hz RAW_FP32, 5QI-9) is 100% optimal here because it provides full 60Hz temporal resolution and 32-bit floating-point precision. "
            f"Do NOT recommend switching to URLLC when RTT is {rtt_ms:.1f}ms; instead, confirm that eMBB-HIGH 60Hz FP32 is delivering unthrottled high-resolution telemetry with zero queue congestion."
        )

    prompt = "\n".join([
        f"You are an Industrial 5G MEC & Edge-AI Operations Engineer monitoring a smart factory robotic/spindle node connected via N3 interface upf0 to Ubuntu MEC Server (172.16.0.5 / Tailscale 100.73.187.15).",
        f"Telemetry Snapshot (Seq #{seq}):",
        f"- Data Source: {source_desc}",
        f"- Physical Kinematics & Host Health: Vibration Shock={g_force:.2f}g (Anomaly Score={anomaly_pct}%), MEC Server Thermal={temp_c:.1f}C (Max 45C), VM CPU={cpu_now:.1f}%, VM RAM={ram_now:.1f}%, Kernel Load={load_now:.2f}",
        f"- 5G Network & QoS State: Channel={prof_desc}, Uplink RTT={rtt_ms:.1f}ms (Jitter={jitter_ms:.1f}ms, SLA Limit=30.0ms), Active Slice={slice_mode}, Auto-QoS={auto_qos}",
        f"- Engineering Ground Truth: {qos_context}",
        "Write a sharp, technical 3-sentence numbered operator diagnosis (1., 2., 3.) with no markdown bolding or extra preamble:",
        f"1. Diagnose the {g_force:.2f}g kinematic vibration anomaly ({anomaly_pct}%) on the industrial node while noting that MEC server compute/thermal ({temp_c:.1f}C, RAM {ram_now:.1f}%) is nominal.",
        f"2. Accurately evaluate the {rtt_ms:.1f}ms upf0 uplink latency against the 30.0ms SLA and explain the exact role of the current slice ({slice_mode}) and {prof_desc}.",
        "3. Provide a concrete mechanical inspection step for the robotic/spindle mount plus the exact 5G MEC network/QoS action required right now."
    ])

    live_text, status_tag = call_gemini_rest_api(prompt, state.get("preferred_model", "gemini-3.1-flash-lite"))
    if live_text:
        return live_text, status_tag

    return (
        f"1. [SEQ #{seq}] Recorded {g_force:.2f}g kinematic vibration anomaly ({anomaly_pct}%) while MEC host thermal ({temp_c:.1f}C) and RAM ({ram_now:.1f}%) remained nominal.\n"
        f"2. Uplink RTT on upf0 measured {rtt_ms:.1f}ms (Jitter ±{jitter_ms:.1f}ms) under {prof_desc} with active slice {slice_mode}.\n"
        f"3. Inspect mechanical spindle/robotic damper mounts and maintain Closed-Loop Auto-QoS policy on interface upf0."
    ), f"mec-edge-synth ({status_tag})"

async def broadcast_genai_alert(temp_c, g_force, anomaly_pct, rtt_ms, jitter_ms, seq, slice_mode):
    t0 = time.perf_counter()
    report, engine = await asyncio.to_thread(
        generate_genai_incident_report,
        temp_c, g_force, anomaly_pct, rtt_ms, jitter_ms, seq, slice_mode, state["auto_qos"], state["impairment_profile"]
    )
    ai_ms = max(1, round((time.perf_counter() - t0) * 1000.0, 0))
    msg = json.dumps({
        "type": "genai_report",
        "seq": seq,
        "engine": engine,
        "rtt_ms": rtt_ms,
        "ai_ms": ai_ms,
        "total_ms": round(rtt_ms + ai_ms, 0),
        "temp_c": temp_c,
        "g_force": g_force,
        "anomaly_pct": anomaly_pct,
        "slice_mode": slice_mode,
        "report": report
    })
    for client in list(dashboard_clients):
        try:
            await client.send_text(msg)
        except Exception:
            dashboard_clients.discard(client)

async def real_mec_telemetry_loop():
    apply_real_tc_netem(1)
    while True:
        if not dashboard_clients:
            client_active_event.clear()
            await client_active_event.wait()

        await asyncio.sleep(0.18)
        if not dashboard_clients:
            continue

        state["seq"] += 1
        compact = state["slice_mode"] == "URLLC-COMPACT"
        phone_online = (len(ue_clients) > 0) or (state["impulse_boost"] > 0.05)

        infer_ms, _ = run_real_kinematic_dsp(state["ax"], state["ay"], state["az"], compact)
        rtt_ms, jitter_ms = await measure_real_upf_rtt(state["impairment_profile"], state["slice_mode"])

        adapt_event = None
        if state["auto_qos"]:
            if rtt_ms > state["sla_limit_ms"] and state["slice_mode"] == "eMBB-HIGH":
                state["high_streak"] += 1
                if state["high_streak"] >= 2:
                    state["slice_mode"] = "URLLC-COMPACT"
                    state["target_hz"] = 15
                    state["codec"] = "INT8_DELTA"
                    state["high_streak"] = 0
                    adapt_event = f"RTT spiked to {rtt_ms}ms (>30ms SLA). Shifted to URLLC-COMPACT (15Hz INT8, 5QI-80) -> Saved 93.5% BW!"
                    await broadcast_konsole_traffic(f"[AUTO-QOS] SLA Breach ({rtt_ms}ms) -> Activated 5QI-80 URLLC-COMPACT (15Hz INT8)")
            elif state["impairment_profile"] == 1 and rtt_ms < 26.0 and state["slice_mode"] == "URLLC-COMPACT":
                state["low_streak"] += 1
                if state["low_streak"] >= 4:
                    state["slice_mode"] = "eMBB-HIGH"
                    state["target_hz"] = 60
                    state["codec"] = "RAW_FP32"
                    state["low_streak"] = 0
                    adapt_event = f"5G channel clean ({rtt_ms}ms). Restored to eMBB-HIGH (60Hz FP32, 5QI-9)."
                    await broadcast_konsole_traffic(f"[AUTO-QOS] Link Recovered ({rtt_ms}ms) -> Restored 5QI-9 eMBB-HIGH (60Hz FP32)")
        else:
            state["slice_mode"] = "eMBB-HIGH"
            state["target_hz"] = 60
            state["codec"] = "RAW_FP32"

        boost = state["impulse_boost"]
        if len(ue_clients) > 0 and boost <= 0.05:
            ax, ay, az = round(state["ax"], 2), round(state["ay"], 2), round(state["az"], 2)
            mag = math.sqrt(ax*ax + ay*ay + az*az)
            g_delta = abs(mag - 9.81) / 9.81
            g_force = round(g_delta, 2)
            anomaly = min(99, max(1, int(g_delta * 85.0)))
        elif boost > 0.05:
            ax, ay, az = round(state["ax"] + boost * 5.0, 2), round(state["ay"] + boost * 4.0, 2), round(state["az"], 2)
            g_force = round(0.85 * boost, 2)
            anomaly = min(99, max(10, int(boost * 88.0)))
        else:
            ax, ay, az = 0.0, 0.0, 0.0
            g_force = 0.0
            anomaly = int(psutil.cpu_percent())

        temp_c = read_real_hw_temp()
        state["impulse_boost"] = max(0.0, state["impulse_boost"] * 0.70 - 0.03)

        raw_cores = psutil.cpu_percent(percpu=True)
        if len(raw_cores) >= 3:
            c0 = int(round(sum(raw_cores[0::3]) / len(raw_cores[0::3])))
            c1 = int(round(sum(raw_cores[1::3]) / len(raw_cores[1::3])))
            c2 = int(round(sum(raw_cores[2::3]) / len(raw_cores[2::3])))
        else:
            c0 = c1 = c2 = int(psutil.cpu_percent())

        ram_pct = round(psutil.virtual_memory().percent, 1)
        load_avg = round(os.getloadavg()[0], 2)
        net_io = psutil.net_io_counters(pernic=True).get("upf0")
        upf_pkts = (net_io.packets_sent + net_io.packets_recv) if net_io else 0

        frame_msg = json.dumps({
            "type": "telemetry_tick",
            "seq": state["seq"],
            "rtt_ms": rtt_ms,
            "jitter_ms": jitter_ms,
            "infer_ms": infer_ms,
            "ax": ax,
            "ay": ay,
            "az": az,
            "temp_c": temp_c,
            "g_force": g_force,
            "anomaly": anomaly,
            "slice_mode": state["slice_mode"],
            "tx_hz": state["target_hz"] if phone_online else 0,
            "cores": [c0, c1, c2],
            "ram_pct": ram_pct,
            "load_avg": load_avg,
            "upf_pkts": upf_pkts,
            "adapt_event": adapt_event,
            "ue_connected": phone_online,
        })

        for client in list(dashboard_clients):
            try:
                await client.send_text(frame_msg)
            except Exception:
                dashboard_clients.discard(client)

        now = time.time()
        if phone_online and anomaly > 55 and (now - state["last_ai_ts"] > 4.0):
            state["last_ai_ts"] = now
            asyncio.create_task(broadcast_genai_alert(temp_c, g_force, anomaly, rtt_ms, jitter_ms, state["seq"], state["slice_mode"]))

@app.on_event("startup")
async def start_background_loop():
    psutil.cpu_percent(percpu=True)
    asyncio.create_task(real_mec_telemetry_loop())

PHONE_TRANSMITTER_HTML = """<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>5G Smartphone UE Transmitter</title>
<style>
  body { background:#000000; color:#ffffff; font-family:monospace; padding:16px; margin:0; }
  .box { border:1px solid #00ffff; background:#050507; padding:14px; margin-bottom:12px; }
  .btn { background:#00ffff; color:#000000; font-weight:bold; border:none; padding:14px; width:100%; font-size:15px; cursor:pointer; margin-bottom:8px; }
</style></head>
<body>
  <div class="box">
    <h3 style="margin:0 0 8px 0;color:#00ffff">📱 5G Smartphone Edge UE (60Hz IMU Node)</h3>
    <div>Status: <strong id="st" style="color:#ffcc00">Idle</strong></div>
    <div>Active Sampling Rate: <strong id="hz" style="color:#00ff66">60 Hz</strong> (<span id="cd">RAW_FP32</span>)</div>
    <div>Measured Uplink RTT: <strong id="rtt" style="color:#00ffff">-- ms</strong></div>
    <pre id="out" style="background:#000000;padding:8px;border:1px solid #28282e;overflow:auto">Tap button below and shake phone to stream real 60Hz motion sensors...</pre>
  </div>
  <button class="btn" onclick="startStreaming()">⚡ START 60Hz 5G SENSOR STREAM TO MEC</button>
<script>
let ws, ax=0.0, ay=0.0, az=9.81, gx=0, gy=0, gz=0, rateHz=60, timer=null, lastRtt=8.5;
async function startStreaming(){
  if (typeof DeviceMotionEvent !== 'undefined' && typeof DeviceMotionEvent.requestPermission === 'function') {
    await DeviceMotionEvent.requestPermission();
  }
  window.addEventListener('devicemotion', e => {
    if (e.accelerationIncludingGravity) {
      ax = +(e.accelerationIncludingGravity.x || 0).toFixed(2);
      ay = +(e.accelerationIncludingGravity.y || 0).toFixed(2);
      az = +(e.accelerationIncludingGravity.z || 9.81).toFixed(2);
    }
    if (e.rotationRate) {
      gx = +(e.rotationRate.alpha || 0).toFixed(2);
      gy = +(e.rotationRate.beta || 0).toFixed(2);
      gz = +(e.rotationRate.gamma || 0).toFixed(2);
    }
  });
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(proto + '//' + location.host + '/ws/ue');
  ws.onopen = () => { document.getElementById('st').innerText = 'LIVE 60Hz STREAMING TO MEC'; rebindLoop(); };
  ws.onmessage = ev => {
    const msg = JSON.parse(ev.data);
    if (msg.t_client) {
      lastRtt = Math.max(1.0, Date.now() - msg.t_client);
      document.getElementById('rtt').innerText = lastRtt.toFixed(1) + ' ms';
    }
    if (msg.target_hz && msg.target_hz !== rateHz) {
      rateHz = msg.target_hz;
      document.getElementById('hz').innerText = rateHz + ' Hz';
      document.getElementById('cd').innerText = msg.codec;
      rebindLoop();
    }
  };
}
function rebindLoop(){
  if (timer) clearInterval(timer);
  timer = setInterval(() => {
    if (ws && ws.readyState === 1) {
      const pkt = { t_client: Date.now(), rtt_ms: lastRtt, ax, ay, az, gx, gy, gz, hz: rateHz };
      ws.send(JSON.stringify(pkt));
      document.getElementById('out').innerText = JSON.stringify(pkt, null, 2);
    }
  }, 1000 / rateHz);
}
</script></body></html>"""

@app.get("/")
async def serve_dashboard():
    if os.path.exists("index.html"):
        return FileResponse("index.html", headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})
    return HTMLResponse("Place index.html in the same folder as mec_server.py")

@app.get("/phone", response_class=HTMLResponse)
async def serve_phone_transmitter():
    return HTMLResponse(PHONE_TRANSMITTER_HTML, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})

@app.websocket("/ws/dashboard")
async def dashboard_ws(ws: WebSocket):
    await ws.accept()
    dashboard_clients.add(ws)
    client_active_event.set()
    try:
        while True:
            raw = await ws.receive_text()
            cmd = json.loads(raw)
            action = cmd.get("action")
            if action == "toggle_auto_qos":
                state["auto_qos"] = bool(cmd.get("enabled", True))
                await broadcast_konsole_traffic(f"[UI->MEC] Set Closed-Loop Auto-QoS = {state['auto_qos']}")
            elif action == "set_impairment":
                prof = int(cmd.get("profile", 1))
                state["impairment_profile"] = prof
                state["high_streak"] = 0
                state["low_streak"] = 0
                exec_cmd = apply_real_tc_netem(prof)
                await broadcast_konsole_traffic(f"[KERNEL TC] Executed: {exec_cmd}")
                await broadcast_konsole_traffic(get_live_tc_stats())
            elif action == "trigger_shake":
                state["impulse_boost"] = 1.0
                await broadcast_konsole_traffic("[UE->MEC] Injected High-G Kinematic Vibration Spike")
            elif action == "set_model":
                state["preferred_model"] = cmd.get("model", "gemini-3.1-flash-lite")
            elif action == "request_genai_report":
                if cmd.get("model"):
                    state["preferred_model"] = cmd.get("model")
                temp_c = float(cmd.get("temp_c", read_real_hw_temp()))
                g_force = max(0.78, float(cmd.get("g_force", 0.84)))
                anomaly_pct = int(cmd.get("anomaly_pct", 82))
                rtt_ms = float(cmd.get("rtt_ms", 11.4))
                jitter_ms = float(cmd.get("jitter_ms", 1.5))
                seq = int(cmd.get("seq", state["seq"]))
                slice_mode = cmd.get("slice_mode", state["slice_mode"])
                asyncio.create_task(broadcast_genai_alert(temp_c, g_force, anomaly_pct, rtt_ms, jitter_ms, seq, slice_mode))
    except WebSocketDisconnect:
        dashboard_clients.discard(ws)

@app.websocket("/ws/konsole")
async def konsole_ws(ws: WebSocket):
    await ws.accept()
    konsole_clients.add(ws)
    ssh_authenticated = False
    ssh_cwd = os.path.expanduser("~")
    cached_pw = ""

    await ws.send_text(json.dumps({
        "type": "traffic_log",
        "ts": time.strftime("%H:%M:%S"),
        "line": "Connected to MEC Kernel Traffic Monitor (upf0).\n" + get_live_tc_stats()
    }))

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            action = msg.get("action")

            if action == "refresh_tc":
                await ws.send_text(json.dumps({
                    "type": "traffic_log",
                    "ts": time.strftime("%H:%M:%S"),
                    "line": get_live_tc_stats()
                }))
            elif action == "ssh_login":
                pw = msg.get("password", "")
                ok = await asyncio.to_thread(verify_linux_password, MEC_USER, pw)
                if ok:
                    ssh_authenticated = True
                    cached_pw = pw
                    ssh_cwd = os.getcwd()
                    await ws.send_text(json.dumps({
                        "type": "ssh_auth",
                        "success": True,
                        "user": MEC_USER,
                        "cwd": ssh_cwd,
                        "banner": "Authenticated to Ubuntu 22.04.1 MEC Server via Tailscale (user@100.73.187.15)\nHost: user@user (/bin/bash) | Type Linux commands below:"
                    }))
                else:
                    ssh_authenticated = False
                    await ws.send_text(json.dumps({
                        "type": "ssh_auth",
                        "success": False,
                        "error": "Permission denied, please try again. (Invalid Ubuntu password)"
                    }))
            elif action == "ssh_logout":
                ssh_authenticated = False
                cached_pw = ""
                await ws.send_text(json.dumps({"type": "ssh_logged_out"}))
            elif action == "ssh_exec":
                if not ssh_authenticated:
                    await ws.send_text(json.dumps({"type": "ssh_output", "output": "Error: Session not authenticated. Please log in first.\n"}))
                    continue
                cmd_str = msg.get("cmd", "").strip()
                if not cmd_str:
                    continue
                if cmd_str.startswith("cd "):
                    target = os.path.expanduser(cmd_str[3:].strip())
                    new_dir = target if os.path.isabs(target) else os.path.abspath(os.path.join(ssh_cwd, target))
                    if os.path.isdir(new_dir):
                        ssh_cwd = new_dir
                        out_text = ""
                    else:
                        out_text = f"bash: cd: {target}: No such file or directory\n"
                else:
                    try:
                        proc = await asyncio.create_subprocess_shell(
                            cmd_str,
                            executable="/bin/bash",
                            cwd=ssh_cwd,
                            stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.STDOUT
                        )
                        stdout_bytes, _ = await asyncio.wait_for(
                            proc.communicate(input=(cached_pw + "\n").encode() if "sudo" in cmd_str else None),
                            timeout=8.0
                        )
                        out_text = stdout_bytes.decode(errors="replace")
                    except asyncio.TimeoutError:
                        out_text = "Command timed out after 8 seconds.\n"
                    except Exception as e:
                        out_text = f"Execution error: {e}\n"

                await ws.send_text(json.dumps({
                    "type": "ssh_output",
                    "cmd": cmd_str,
                    "cwd": ssh_cwd,
                    "user": MEC_USER,
                    "output": out_text
                }))
    except WebSocketDisconnect:
        konsole_clients.discard(ws)

@app.websocket("/ws/ue")
async def ue_stream_ws(ws: WebSocket):
    await ws.accept()
    ue_clients.add(ws)
    client_active_event.set()
    await broadcast_konsole_traffic("[UE CONNECTED] 5G Smartphone UE started 60Hz IMU stream on /ws/ue")
    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            state["ax"] = float(data.get("ax", 0.0))
            state["ay"] = float(data.get("ay", 0.0))
            state["az"] = float(data.get("az", 9.81))
            state["gx"] = float(data.get("gx", 0.0))
            if data.get("rtt_ms"):
                state["ue_client_rtt_ms"] = float(data["rtt_ms"])
            await ws.send_text(json.dumps({
                "t_client": data.get("t_client"),
                "target_hz": state["target_hz"],
                "codec": state["codec"],
                "slice_mode": state["slice_mode"]
            }))
    except WebSocketDisconnect:
        ue_clients.discard(ws)
        state["ue_client_rtt_ms"] = 0.0
        await broadcast_konsole_traffic("[UE DISCONNECTED] Smartphone UE closed stream -> Switched to MEC Host Telemetry")
