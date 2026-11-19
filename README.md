# ⚡ EDGE-CTL // 5G MEC Real-Time Hardware Telemetry & Anomaly Monitoring Center

> **Real-Time Industrial Hardware Telemetry & Fault Monitoring over Private 5G MEC — combining dual-source hardware sensing (60Hz Smartphone IMU + Ubuntu MEC Server telemetry), Gemini Edge-AI root-cause diagnostics, isolated Linux kernel `upf0` traffic emulation (`tc netem`), and automatic QoS protection (`<30.0 ms` URLLC SLA).**

---

## 📌 1. Project Overview: Why 5G Hardware Telemetry Monitoring?

In modern **Industry 4.0** factories, autonomous warehouses, and edge micro-datacenters, critical hardware (CNC spindles, robotic arms, mobile robots, and server racks) must be monitored continuously for mechanical shock, abnormal vibration, overheating, or compute overload.

`EDGE-CTL` is a **5G Multi-Access Edge Computing (MEC) Hardware Telemetry Monitoring Center** deployed on an Ubuntu 22.04 MEC server. It provides:

1. **High-Resolution Dual-Source Monitoring:** Ingests live **60Hz** physical vibration/acceleration telemetry from a connected 5G hardware node (`/phone`) alongside live MEC server hardware metrics (`psutil` CPU cores, RAM usage, kernel load average, thermal zones, and `upf0` packet counters).
2. **Real-Time Anomaly Detection & Edge-AI Diagnostics:** Continuously evaluates vibration G-force, thermal thresholds, and compute load. Whenever an anomaly crosses its safety threshold (or manually via `[g]` / `[s]`), live telemetry is sent to **Google Gemini (`gemini-3.1-flash-lite`)** to generate a concise 3-sentence root-cause diagnosis and remediation step.
3. **Resilient 5G Transport (Adaptive QoS):** Keeps telemetry flowing under the **30.0 ms URLLC deadline** even when real-world 5G impairments—**gNodeB handovers**, **mmWave blockage**, or **cell PRB congestion**—degrade the wireless link.

---

## 🏭 2. Real-World (IRL) Industrial Use Cases

| Deployment Scenario                            | Monitored Hardware Node                                                               | Critical Telemetry & Anomalies                                                  | Role of 5G MEC + Adaptive QoS                                                                                     |
| :--------------------------------------------- | :------------------------------------------------------------------------------------ | :------------------------------------------------------------------------------ | :---------------------------------------------------------------------------------------------------------------- |
| **A. Smart Factory CNC Spindle & Robotic Arm** | 60Hz triaxial vibration & shear sensor mounted on a CNC spindle or 6-axis welding arm | Bearing chatter / mechanical shock (`>0.50g`), motor thermal drift              | Compresses telemetry during crane-induced **mmWave Blockage (`[3]`)** so emergency PLC feed-hold stays `<30.0 ms` |
| **B. Autonomous Warehouse AMR / AGV Fleet**    | Roaming mobile robots streaming 60Hz kinematic odometry                               | Floor-impact shock, sudden deceleration shear (`ACC_X/Y`)                       | Prevents UPF packet queue stalls during **gNB Cell Handovers (`[2]`)** and **Cell Congestion (`[4]`)**            |
| **C. 5G Cell-Site MEC Micro-Datacenter**       | Ruggedized Ubuntu MEC server (`172.16.0.5`) + rack seismic sensor                     | Server RAM, per-core CPU load, thermal throttling (`>45°C`), `upf0` queue drops | Correlates OS/kernel health with physical chassis shock and enables live `/bin/bash` remediation via built-in SSH |

---

## ❓ 3. Architecture & Technical Q&A

### Q1. Why didn't we use the Anubhav board, and how does the 5G smartphone replace it?

- **Limitations of the Anubhav Board:** During testing, the Anubhav board exhibited uplink instability when transmitting live hardware telemetry back to the MEC server. More importantly, it introduces a **middleman bottleneck** for adaptive control: when the MEC server commands a rate/encoding change (`60Hz FP32` → `15Hz INT8`), the command travels to the Anubhav board first, which must then forward it over serial/UART to the attached hardware before the sensor reconfigures. That extra relay hop adds latency overhead and produces stale telemetry packets during fast anomalies.
- **1-to-1 Replacement with a 5G Smartphone UE (`/phone`):** A 5G smartphone already integrates both a **native 5G modem** and **calibrated built-in MEMS sensors** (triaxial accelerometer + gyroscope), removing external sensor wiring and eliminating the middleman relay. Downlink adaptation commands arrive directly at the sensor loop over WebSockets (`/ws/ue`).
- **Mapping to Production Hardware:** In an actual factory deployment, the smartphone is simply replaced by an industrial sensor module with an onboard 5G modem hardwired directly to the MEMS transducer—running the exact same `/ws/ue` WebSocket protocol.

### Q2. How does the phone connect to the server and stream real (non-synthetic) hardware data?

1. The smartphone opens `http://<MEC_SERVER_IP>:8000/phone` (over LAN `172.16.0.5` or peer-to-peer Tailscale mesh `100.73.187.15`).
2. Tapping **START 60Hz 5G SENSOR STREAM TO MEC** registers the browser's hardware **`DeviceMotionEvent`** listener (`accelerationIncludingGravity` for `ax, ay, az` and `rotationRate` for `gx, gy, gz`) and opens a full-duplex WebSocket to **`/ws/ue`**.
3. On the server (`mec_server.py`), `ue_stream_ws` registers the active phone in `ue_clients`, switching Panel 06 from Ubuntu VM host stats (`mec_node_telemetry`) to live mobile sensor vectors (`ue_imu_stream`). Physically shaking the phone immediately spikes `ACC_X/Y` and `VIB_G_FORCE` in real time.

### Q3. Why not keep the sensor in compressed `15Hz INT8` mode all the time?

Compressed `15Hz INT8_DELTA` cuts bandwidth by **93.5%** (`681.6 Kbps` → `44.2 Kbps`), which is ideal for surviving wireless congestion—but quantization to 8-bit integers and downsampling to 15Hz discards fine-grained high-frequency vibration signatures needed for early mechanical fault detection. By staying on **`eMBB-HIGH` (`60Hz RAW_FP32`, `5QI-9`)** whenever the 5G link is healthy (`<30 ms`), operators get full diagnostic fidelity and only trade resolution for survival during active radio impairments.

### Q4. What is the difference between Open-Loop and Closed-Loop (Auto-QoS) operation?

| Mode            | Toggle (`[c]`)  | Behavior Under 5G Impairments (`[2]`–`[4]`)                                                                         | Result on `UL_RTT` & Bandwidth                                                                                                                                    |
| :-------------- | :-------------: | :------------------------------------------------------------------------------------------------------------------ | :---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Open-Loop**   | `Auto-QoS: OFF` | Sensor blindly keeps blasting `60Hz RAW_FP32` (`681.6 Kbps`) into a degraded/congested pipe                         | `upf0` queue overflows (bufferbloat); RTT spikes to **`49–125 ms`** (**SLA Breach**)                                                                              |
| **Closed-Loop** | `Auto-QoS: ON`  | After 2 ticks `>30.0 ms`, MEC server sends downlink `{target_hz: 15, codec: "INT8_DELTA"}` (`5QI-80`) over `/ws/ue` | Bandwidth drops **93.5%** (`44.2 Kbps`), queue drains, RTT recovers to **`~18–20 ms`** (**SLA Compliant**). Auto-restores `60Hz FP32` once channel clears (`[1]`) |

### Q5. How is `upf0` configured without disrupting the server's real network, and how is `tc netem` run securely?

- **Isolated Virtual N3 Interface (`upf0`):** Instead of applying `tc netem` delays to `eth0` or `tailscale0` (which would lag SSH and the web UI itself), we load Linux kernel modules `dummy` and `sch_netem` to create an isolated virtual 5G User Plane Function interface **`upf0`** (`10.45.0.1/24`). All kernel packet-delay, jitter, loss, and rate-cap rules execute strictly on `upf0`.
- **Least-Privilege `sudo`:** Rather than running FastAPI as `root`, the service runs as unprivileged user `user` and uses `/etc/sudoers.d/mec-tc` to permit passwordless `sudo -n` **exclusively** for `/sbin/tc` and `/usr/sbin/tc`.

### Q6. How does the Gemini Edge-AI Incident Synthesizer work?

When vibration/anomaly scores cross the safety threshold (or when the operator presses `[g]` or `[s]`), `mec_server.py` sends a structured snapshot—data source (Phone IMU vs. Host VM), G-force shock, thermal °C, RAM/CPU load, active `tc netem` profile, `upf0` RTT, and slice mode—to Google's Gemini REST API (`gemini-3.1-flash-lite` with automatic model failover). The state-aware prompt ensures Gemini never gives contradictory advice (e.g., it confirms `eMBB-HIGH` is optimal at `10 ms`, flags Open-Loop breaches when `Auto-QoS` is OFF, and confirms mitigation when `URLLC-COMPACT` is active).

---

## 🖥️ 4. 6-Panel Monochrome TTY Operations Dashboard

1. **`01 // ul_telemetry_link`:** High-DPI 16-second rolling RTT oscilloscope with **Y-Axis Latency (`0–75 ms`)**, **X-Axis Time (`-16s` to `NOW`)**, interactive hover crosshair, `30.0 ms` SLA line, and `gNB-N78-MEC01` PHY telemetry (`RSRP/SINR`, `256-QAM`, `5QI`, `PRB Alloc`, `4x4 UL-MIMO`).
2. **`02 // konsole_&_audit`:** 3-tab operational terminal: `0:audit_log` (QoS & AI chronology), `1:upf0_tc` (live `tc -s qdisc` & `ip -s -br link` stats), and `2:ssh` (password-authenticated interactive `/bin/bash` shell with fullscreen `[f]` and one-click TC macros).
3. **`03 // netem_ctrl`:** Real Linux kernel `tc qdisc netem` impairment injector (`[1]` Clean 5G, `[2]` gNB Handover `+28ms ±9ms 0.8% loss`, `[3]` mmWave Block `+56ms ±18ms 3.8% loss`, `[4]` PRB Congest `150kbit +38ms ±12ms`).
4. **`04 // mec_pipeline`:** Per-core Ubuntu VM CPU load (`C0 upf_rx`, `C1 fft_dsp`, `C2 sla_ctl`), uplink bitrate & compression savings (`93.5% SAVED`), and 4-stage E2E loop breakdown (`NR_Air_UL`, `UPF_Queue`, `MEC_Infer`, `DL_QoS_Ack`).
5. **`05 // mec_genai_ops`:** Live Gemini Edge-AI 3-sentence incident synthesizer with model selector and persistent `◀ 1/N ▶` report history.
6. **`06 // mec_node_telemetry` / `ue_imu_stream`:** Auto-switching hardware monitor displaying Ubuntu MEC VM host health when idle and live 60Hz smartphone IMU vectors when connected.
7. **`TUI-DOCS // README.md` (`[d]` / `[?]`):** Fullscreen searchable documentation and 48-term keyword dictionary with per-panel hover summaries.

---

## ⌨️ 5. Keyboard Shortcuts

|        Key        | Action                                                                                                                      |
| :---------------: | :-------------------------------------------------------------------------------------------------------------------------- |
| **`[1]` – `[4]`** | Inject Linux kernel `tc netem` 5G channel profile on `upf0` (`[1]` Clean, `[2]` Handover, `[3]` Blockage, `[4]` Congestion) |
|     **`[c]`**     | Toggle Adaptive QoS (`ON: Closed-Loop` <-> `OFF: Open-Loop`)                                                                |
|     **`[s]`**     | Inject High-G hardware shock anomaly + trigger Gemini AI report                                                             |
|     **`[g]`**     | Synthesize an instant Gemini Edge-AI report for the current hardware & network state                                        |
|     **`[k]`**     | Switch Panel 02 between `1:upf0_tc` and `2:ssh` interactive shell                                                           |
|     **`[f]`**     | Toggle fullscreen terminal modal (`Esc` to exit)                                                                            |
| **`[d]` / `[?]`** | Open fullscreen TUI Docs & live keyword search (`/` to search)                                                              |
|   **`[SPACE]`**   | Pause / Resume oscilloscope graph                                                                                           |

---

## 🚀 6. Complete Installation & Deployment Guide (Ubuntu 22.04 MEC VM)

### Step 1: Clone Repository & Set Up Python Virtual Environment

{F}bash
git clone https://github.com/bytekandy/5g_usecase.git ~/grp21_usecase
cd ~/grp21_usecase

python3 -m venv venv
source venv/bin/activate
pip install fastapi "uvicorn[standard]" websockets psutil python-dotenv
{F}

### Step 2: Configure Gemini API Key

{F}bash
cp .env.example .env
nano .env

# Set: GEMINI_API_KEY=your_actual_gemini_api_key

{F}

### Step 3: Grant Least-Privilege Passwordless `sudo` for Kernel `tc`

Create `/etc/sudoers.d/mec-tc` so the backend can update `upf0` qdisc rules without running as root:
{F}bash
echo "user ALL=(ALL) NOPASSWD: /usr/sbin/tc, /sbin/tc" | sudo tee /etc/sudoers.d/mec-tc
sudo chmod 440 /etc/sudoers.d/mec-tc
{F}

### Step 4: Initialize Virtual 5G N3 Interface (`upf0`) Manually (for Testing)

{F}bash
sudo modprobe dummy
sudo modprobe sch_netem
sudo ip link add upf0 type dummy 2>/dev/null || true
sudo ip addr add 10.45.0.1/24 dev upf0 2>/dev/null || true
sudo ip link set upf0 up
{F}

### Step 5: Deploy as a Persistent `systemd` Service (`mec-server.service`)

Create `/etc/systemd/system/mec-server.service` so `upf0` and Uvicorn start automatically on every boot:
{F}ini
[Unit]
Description=5G MEC Real-Time Hardware Telemetry & GenAI Operations Center
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=simple
User=user
WorkingDirectory=/home/user/grp21_usecase
EnvironmentFile=-/home/user/grp21_usecase/.env
ExecStartPre=+/usr/sbin/modprobe dummy
ExecStartPre=+/usr/sbin/modprobe sch_netem
ExecStartPre=-+/usr/sbin/ip link add upf0 type dummy
ExecStartPre=-+/usr/sbin/ip addr add 10.45.0.1/24 dev upf0
ExecStartPre=+/usr/sbin/ip link set upf0 up
ExecStart=/home/user/grp21_usecase/venv/bin/uvicorn mec_server:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
{F}

Enable and start the service:
{F}bash
sudo systemctl daemon-reload
sudo systemctl enable --now mec-server.service
sudo systemctl status mec-server.service
{F}

### Step 6: Access the Dashboard & Connect the 5G Smartphone UE

- **Operations Dashboard (Browser):** `http://<MEC_SERVER_IP>:8000` (Tailscale: `http://100.73.187.15:8000` | Lab LAN: `http://172.16.0.5:8000`)
- **Smartphone 60Hz Hardware Transmitter:** `http://<MEC_SERVER_IP>:8000/phone`
- _(Optional Remote Access)_ Install [Tailscale](https://tailscale.com/) on the MEC VM, laptop, and smartphone to connect over a direct peer-to-peer WireGuard mesh outside the lab network.
