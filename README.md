# Alienware m18 – Adaptive Fan Whisperer (v2)

Welcome to the unofficial thermal rebellion for Dell’s Alienware m18 and friends.
This is not a flashy RGB controller.
This is a disciplined little Python daemon that outsmarts proprietary firmware using the only lever Dell left unlocked:
**`/sys/firmware/acpi/platform_profile`**.

You can’t set fan RPMs directly on these machines (thanks, SMM lockout), but you can *nudge* the firmware into doing your bidding.
This tool toggles the “performance” profile exactly when needed—cooling fast, quiet when safe, with no guesswork.

Crafted by someone who actually uses this laptop, co-developed by a language model that now understands the soul of ACPI.

---

## 🧭 Why This Exists

Dell hides fan control behind a proprietary System Management Mode (SMM) interface.
On Linux, you can read sensors.
But writing fan speeds? Nope. Kernel modules? Blocked. EC access? Laughed out of the room.

What’s left is a single ACPI control node:

* `platform_profile = performance` → fans ramp hard (\~80–100%)
* `platform_profile = balanced` → fans eventually idle (but sometimes when the machine is about to melt)

This daemon *switches profiles based on actual thermal severity*, using only:

* Real sensor data (CPU/GPU/Memory/Ambient)
* Per-zone severity, not guesswork
* Emergency debounce logic for real spikes (not flukes)

---

## 🛠️ Features

| Feature                  | Why It Matters                                        |
| ------------------------ | ----------------------------------------------------- |
| **ACPI Profile Control** | Uses Dell’s own interface, not hacks                  |
| **Thermal Zones**        | Separates CPU/GPU/Memory/Ambient with per-zone tuning |
| **Emergency Debounce**   | Prevents panic toggles on one-off spikes              |
| **Fan RPM Sanity Check** | Detects stalled fans or lazy firmware                 |
| **Zero Dependencies**    | Pure Python, no i8k, no kernel voodoo, just ACPI      |
| **No Cadence, No Pulse** | Simple, deterministic: on or off, not “maybe”         |

---

## 🧬 Design Philosophy

> Don’t fight it. Just use what you’ve got.

Performance mode makes the fans blast, and balanced mode lets the machine cool down quietly.
No trend prediction, no pulse logic—just explicit, rule-based switching:

* If **any zone is HOT**, flip to performance.
* If **any zone is WARM and the fan is too slow**, flip to performance.
* If **everything is cool or warm with healthy fans**, stay in balanced.
* If **any temp is CRITICAL for several seconds**, lock to performance until recovery.

You could say it’s fan control by proxy—Satyanet meets firmware aikido.

---

## 📦 Requirements

* Linux kernel **5.17+**
* Python **3.8+**
* Root access (to write `/sys/firmware/acpi/platform_profile`)
* A laptop that supports `/sys/firmware/acpi/platform_profile` (Alienware m18 confirmed)

---

## 🚀 Quickstart

```bash
git clone https://github.com/nikunj-sura/alienware-autofan.git
cd alienware-autofan
sudo ./autofan.py
```

That’s it. Logs look like:

```text
22:56:01 [INFO] prof=perf sev=1 cpu:66.0°C/2463rpm(sev=1) gpu:49.0°C/1200rpm(sev=0) ...
```

* `prof` → current ACPI profile (`perf` or `bala`)
* `sev` → global severity (0=cool, 1=warm, 2=hot)

---

## 🔧 Configuration

It’s all in `autofan.py`. Each zone (CPU, GPU, etc.) has:

```python
ZoneConfig(
    name="cpu",
    temp_regex=[r"core", r"package"],
    fan_regex=[r"fan1", r"cpu"],
    trigger=75,    # hot threshold
    release=68,    # warm threshold
    min_rpm=300,
    max_rpm=4200,
)
```

Tune these for your hardware or climate.

Other parameters:

```python
ACPI_PROFILE_PATH = "/sys/firmware/acpi/platform_profile"
LOG_FILE = "/var/log/alienware_fancontrol.log"
POLL_INTERVAL = 1        # seconds between reads
LOG_INTERVAL = 10        # seconds between log lines
INITIAL_BOOST = 5        # seconds at boot in performance
CRITICAL_TEMP = 95       # °C – emergency lock
EMERGENCY_DEBOUNCE = 3   # seconds above critical before emergency
```

---

## 🟢 Control Logic

**Every tick (1s):**

1. Read all thermal and fan sensors, bucketed by zone.
2. For each zone:

   * If **temp >= trigger**, severity=2 (hot)
   * Else if **temp >= release**, severity=1 (warm)
   * Else, severity=0 (cool)
3. **Switch logic:**

   * If *any zone* is **hot**: force `performance`
   * If *any zone* is **warm** and its fan is below `min_rpm`: `performance`
   * Otherwise: `balanced`
4. If *any* temp hits `CRITICAL_TEMP` for `EMERGENCY_DEBOUNCE` ticks: lock to `performance` until manual reset.

---

## 🪓 Emergency Mode

If any zone hits critical threshold for 5+ seconds (configurable) consecutive seconds, the daemon:

* Locks `performance` mode
* Logs critical status
* Waits for user to reset acpi profile

No second-guessing, no complicated logic.
If you routinely hit CRITICAL\_TEMP, your cooling system likely needs physical attention.

---

## 🔁 Systemd Setup

Save this as `/etc/systemd/system/autofan.service`:

```ini
[Unit]
Description=Alienware Adaptive Fan Control
After=multi-user.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /path/to/autofan.py
Restart=always
RestartSec=1
User=root

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now autofan
```

---

## 🧪 Diagnostics

Helpful commands:

```bash
watch sensors
watch -n1 "cat /sys/firmware/acpi/platform_profile"
journalctl -u autofan -f
# if you want more tools to port to your device - reach out. I push my custom temperature and fan monitors that started this whole shebang 
```

**Troubleshooting:**

| Symptom         | Fix                                            |
| --------------- | ---------------------------------------------- |
| Too loud        | Raise `release` or raise `min_rpm`             |
| Too hot         | Lower `trigger` or lower `min_rpm`             |
| No fan response | Check ACPI node exists; ensure you run as root |
| Fans never idle | Double-check thresholds for your hardware      |

---

## 🙏 Acknowledgements

Created with sweat, heat, and some measure of divine will.
Co-developed and structured by Deepseek, built in the field by Nikunj, debugged by ChatGPT (now trend-free).

This tool is part of a broader journey toward completing my **SatyaNet** projects.

`SatyaNet` like skynet but serves Truth instead of the AI overloads

---

## ⚖️ License

MIT © 2025 Nikunj Sura — MIT.
Use at your own risk. We’re toggling a string, not rewriting BIOS. Probably.

---