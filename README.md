# ⚡ EDGE-CTL // 5G MEC Closed-Loop SLA & GenAI Operations Center

> **Real-Time 5G N3 (`upf0`) Kernel Traffic Shaping, Sub-Second Closed-Loop Slice Adaptation (`eMBB` <-> `URLLC`), Dual-Source Hardware Telemetry, and State-Aware Gemini Edge-AI Incident Synthesis.**

---

## 📌 1. The Engineering Problem: The 5G Edge Telemetry Paradox

In mission-critical **Industry 4.0** and **Private 5G (Band n78)** deployments, industrial sensors stream high-frequency, uncompressed kinematic telemetry (**60Hz `RAW_FP32` at 681.6 Kbps**) over a standard **5G eMBB (`5QI-9`)** slice to maximize diagnostic precision at the Multi-Access Edge Computing (MEC) server.

* **Open-Loop Failure (`Auto-QoS: OFF`):** Real-world wireless channels suffer from transient **gNodeB handovers (`+28ms`)**, **mmWave NLOS blockage (`+56ms`)**, and **PRB cell congestion (`150kbit` rate cap)**. Under Open-Loop operation, an unthrottled 60Hz `FP32` sensor continues flooding the degraded radio link, causing severe **UPF N3 (`upf0`) kernel queue bufferbloat**. Uplink Round-Trip Time (`UL_RTT`) spikes to **49.5 ms – 125.4 ms**, violating the hard **30.0 ms URLLC safety SLA**.
* **Closed-Loop MEC Solution (`Auto-QoS: ON`):** `EDGE-CTL` continuously monitors the Linux kernel `upf0` N3 tunnel and live WebSocket RTT. When `UL_RTT` breaches **30.0 ms** for 2 consecutive ticks, the MEC controller dispatches a sub-second downlink command over `/ws/ue`, shifting the active slice to **`URLLC-COMPACT` (`15Hz INT8_DELTA`, `5QI-80`)**. This slashes uplink bitrate by **93.5%** (`681.6 Kbps` -> `44.2 Kbps`), drains the `upf0` packet queue, and pulls latency back down to **~18–19 ms (`<30.0 ms SLA COMPLIANT`)** while the physical channel impairment remains active.

---

## 🏭 2. Real-World (IRL) Deployment Scenarios

| Scenario | Edge Sensor Node (`/phone`) | 5G Channel Impairment (`upf0`) | Closed-Loop MEC Action (`<30ms SLA`) |
| :--- | :--- | :--- | :--- |
| **A. Smart Factory CNC Spindle & Robotic Interlock** | 60Hz triaxial vibration & shear sensor mounted on a CNC milling spindle or 6-axis robotic arm | `[3] mmWave Block` (`+56ms` delay, `3.8%` loss) from moving overhead factory cranes | Compresses stream to `15Hz INT8` (`5QI-80`) so `>0.50g` bearing-chatter alerts trigger PLC feed-hold in `<30ms` |
| **B. Autonomous Warehouse AMR / AGV Fleet** | 60Hz kinematic odometry & shock sensor on roaming mobile robots | `[2] gNB Handover` (`+28ms`, `±9ms` jitter) & `[4] PRB Congest` (`150kbit` cap) | Prevents UPF packet queue stalls during cell handovers so collision-avoidance braking stays deterministic |
| **C. 5G Cell-Site MEC Micro-Datacenter** | Ruggedized Ubuntu MEC server (`172.16.0.5`) + rack seismic sensor | N3 GTP-U backhaul congestion & thermal/CPU load spikes | Correlates VM RAM, kernel load, thermal zones, and `upf0` packet counters with live `/bin/bash` SSH remediation |

---

## 🖥️ 3. 6-Panel Monochrome TTY Architecture

The dashboard is engineered with a zero-dependency **Pure TTY Pitch-Black (`#000000`) & Crisp ANSI Terminal** layout (`100vh` locked grid):

1. **`01 // ul_telemetry_link` (5G N3 RTT Oscilloscope & Radio PHY):**
   * High-DPI rolling 16-second (`90-sample`) oscilloscope with **Y-Axis Latency (`0ms–75ms`)**, **X-Axis Time (`-16s` to `NOW`)**, interactive mouse-hover crosshair readouts, and `gNB-N78-MEC01` PHY parameters (`RSRP/SINR`, `256-QAM`, `5QI`, `PRB Alloc`, `4x4 UL-MIMO`).
2. **`02 // konsole_&_audit` (Full-Height Audit Stream, Kernel TC & Interactive `/bin/bash` SSH):**
   * **`0:audit_log`:** Real-time chronology of Auto-QoS adaptations, kernel netem injections, and AI syntheses.
   * **`1:upf0_tc`:** Live output of `tc -s qdisc show dev upf0` and `ip -s -br link show dev upf0`.
   * **`2:ssh (100.73.187.15)`:** Password-authenticated interactive `/bin/bash` shell on the Ubuntu MEC VM with command history (`Up`/`Down`), fullscreen mode (`[f]`), and one-click `tc netem` macro buttons.
3. **`03 // netem_ctrl` (Linux Kernel `upf0` Impairment Injector):**
   * Executes real `sudo tc qdisc replace dev upf0 root netem ...` rules inside the Linux kernel across 4 presets (`[1]` Clean 5G, `[2]` gNB Handover, `[3]` mmWave Block, `[4]` PRB Congest).
4. **`04 // mec_pipeline` (Edge Compute, Bitrate Savings & E2E Loop):**
   * Tracks per-core Ubuntu VM CPU load (`C0 upf_rx`, `C1 fft_dsp`, `C2 sla_ctl`), wireless bandwidth savings (`93.5% SAVED`), and 4-stage E2E latency breakdown (`NR_Air_UL`, `UPF_Queue`, `MEC_Infer`, `DL_QoS_Ack`).
5. **`05 // mec_genai_ops` (State-Aware Gemini Incident Synthesizer):**
   * Connects to Google's lightweight models (`gemini-3.1-flash-lite`, `gemini-3.1-flash-lite-preview`, `gemini-3.5-flash-lite`, `gemma-4-26b-a4b-it`) to synthesize a 3-sentence operator diagnosis with persistent `◀ 1/N ▶` report history.
6. **`06 // mec_node_telemetry` / `ue_imu_stream` (Dual-Source Hardware Telemetry):**
   * Automatically switches between **Ubuntu MEC VM Host Telemetry** (`MEC_RAM_USAGE`, `MEC_LOAD_AVG`, `upf0 Pkts`, `NODE_TEMP`, `CPU_LOAD`) when idle and **Live 60Hz Smartphone IMU Telemetry** (`ACC_X/Y` shear, `VIB_G_FORCE` shock, `MEC_ANOMALY_SCORE`) when a phone connects to `/phone`.
7. **`TUI-DOCS // README.md` (`[d]` or `[?]`):**
   * Built-in fullscreen documentation viewer with per-panel hover summaries (`[?]`) and an instant keyword search engine covering all 48 metrics and acronyms.

---

## ⌨️ 4. Keyboard Shortcuts & Controls

| Shortcut | Action |
| :---: | :--- |
| **`[1]`** | Apply Profile `[1] Clean 5G NR LOS` (`sudo tc qdisc del dev upf0 root`) |
| **`[2]`** | Inject Profile `[2] gNB Handover Spike` (`+28ms` delay, `±9ms` jitter, `0.8%` loss) |
| **`[3]`** | Inject Profile `[3] mmWave NLOS Blockage` (`+56ms` delay, `±18ms` jitter, `3.8%` loss) |
| **`[4]`** | Inject Profile `[4] Slice PRB Congestion` (`150kbit` rate cap, `+38ms` delay) |
| **`[c]`** | Toggle **Closed-Loop Auto-QoS** (`ON: ACTIVE` <-> `OFF: OPEN-LOOP`) |
| **`[s]`** | Trigger High-G Kinematic Vibration Anomaly + Synthesize Live Gemini Report |
| **`[g]`** | Run Manual **Gemini Edge-AI Incident Report** on Current Telemetry Snapshot |
| **`[k]`** | Switch Panel 02 between `1:upf0_tc` and `2:ssh` Interactive Terminal |
| **`[f]`** | Toggle Fullscreen Terminal Modal (`Esc` to exit) |
| **`[d]` / `[?]`** | Open Fullscreen **TUI Docs & Interactive Keyword Search** (`/` to search) |
| **`[SPACE]`** | Pause / Resume Real-Time Telemetry Oscilloscope |

---

## 🚀 5. Quick Start & Installation

### 1. Clone & Configure Environment
```bash
git clone <YOUR_GITHUB_REPO_URL>
cd 5g_usecase
python3 -m venv venv
./venv/bin/pip install fastapi "uvicorn[standard]" websockets psutil python-dotenv
cp .env.example .env
# Add your GEMINI_API_KEY inside .env
```

### 2. Initialize Virtual 5G UPF Interface (`upf0`)
```bash
sudo modprobe dummy
sudo modprobe sch_netem
sudo ip link add upf0 type dummy 2>/dev/null || true
sudo ip addr add 10.45.0.1/24 dev upf0 2>/dev/null || true
sudo ip link set upf0 up
```

### 3. Run the MEC Operations Server
```bash
./venv/bin/uvicorn mec_server:app --host 0.0.0.0 --port 8000
```

* **Main Operations Center:** `http://<MEC_SERVER_IP>:8000` (Tailscale: `http://100.73.187.15:8000` / Lab: `http://172.16.0.5:8000`)
* **Mobile 60Hz IMU Transmitter:** `http://<MEC_SERVER_IP>:8000/phone`
