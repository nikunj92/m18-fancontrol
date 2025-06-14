# Alienware m18 – Adaptive Fan Whisperer (v2)

Welcome to the unofficial thermal rebellion for Dell’s Alienware m18 and friends. This is not a flashy RGB controller. This is a disciplined little Python daemon that outsmarts proprietary firmware using the only lever Dell left unlocked: **`/sys/firmware/acpi/platform_profile`**.

You can’t set fan RPMs directly on these machines (thanks SMM lockout), but you can *nudge* the firmware into doing your bidding. This tool pulses the “performance” profile at just the right moments to keep your system cool without sounding like a leaf blower.

Crafted by a human who actually lives with this laptop, and co-developed by an AI who’s learned to speak both ACPI and sarcasm.

---

## 🧭 Why This Exists

Let’s be blunt.

Dell hides fan control behind a proprietary System Management Mode (SMM) interface. On Linux, you can read sensors just fine. But writing fan speeds? Nope. Kernel modules? Blocked. EC access? Laughed out of the room. SMBIOS? Dead end.

What’s left is a single ACPI control node:

* `platform_profile = performance` → fans ramp hard (\~80–100 %)
* `platform_profile = balanced` → fans eventually idle (but often too late)

This daemon *flips that switch on and off intelligently*, based on:

* Real sensor data
* Calculated temperature trends (with timestamps)
* Custom zones (CPU/GPU/ambient/memory)
* Hysteresis and cooldown logic
* Emergency debounce for real spikes (not flukes)

---

## 🛠️ Features

| Feature                      | Why It Matters                                        |
| ---------------------------- | ----------------------------------------------------- |
| **ACPI Pulse Control**       | Uses Dell’s own knobs against it                      |
| **Thermal Zones**            | Separates CPU/GPU/Memory/Ambient with per-zone tuning |
| **Trend-Aware Cadence**      | Doesn’t just react to heat — it predicts it           |
| **Emergency Debounce**       | Prevents panic toggles on one-off spikes              |
| **Global Fan Sanity Check**  | Detects stalled fans or sensors                       |
| **Hysteresis, Not Hysteria** | Avoids flapping between states                        |
| **Zero Dependencies**        | Pure Python, no `i8k` hacks, no kernel voodoo         |

---

## 🧬 Design Philosophy

> Stop fighting firmware. Start orchestrating it.

After a wasted month trying to reverse-engineer Dell’s Embedded Controller (EC), patching `i8k`, and spelunking through ACPI tables… the real insight came:

**Let the firmware do its job — just do a better job of telling it when.**

This daemon reads every thermal sensor on `/sys/class/hwmon`, buckets them by purpose, and builds a real-time model of how fast temps are rising and how close we are to the danger zone. Then it toggles profiles accordingly — no more, no less.

You could say it’s fan control by proxy. We prefer: **DharmaOS meets firmware aikido**.

---

## 📦 Requirements

* Linux kernel **5.17+**
* Python **3.8+**
* Root access (to write to ACPI node)
* A laptop that supports `/sys/firmware/acpi/platform_profile` (Alienware m18 confirmed)

---

## 🚀 Quickstart

```bash
git clone https://github.com/nikunj-sura/alienware-autofan.git
cd alienware-autofan
sudo ./autofan.py
```

That’s it. You’ll see logs like:

```text
22:56:01 [INFO] prof=perf sev=1 cad=on=4/off=10 cpu:66.0°C/2463rpm(t0.00) ...
```

* `prof` → current ACPI profile (`perf` or `bala`)
* `sev` → global severity (0=cool, 1=warm, 2=hot)
* `cad` → pulse timing (on/off seconds)
* `tX.XX` → thermal trend slope (per zone)

---

## 🔧 Configuration

It’s all in `autofan.py`. Every zone (CPU, GPU, etc.) has:

```python
ZoneConfig(
    name="cpu",
    temp_regex=[r"core", r"package"],
    fan_regex=[r"fan1", r"cpu"],
    trigger=75,
    release=68,
    min_rpm=300,
    max_rpm=4200,
)
```

You can fine-tune thresholds per zone. Want memory to chill harder? Drop its trigger temp. Want less fan toggling? Increase the `release`.

Global tunables:

```python
CRITICAL_TEMP = 95         # °C emergency lock
TREND_SENSITIVITY = 0.3    # Higher = more reactive
BASE_CADENCE = {
    0: {"on": 0, "off": 9999},  # Cool zone
    1: {"on": 1, "off": 14},    # Warm zone
    2: {"on": 4, "off": 8},     # Hot zone
}
```

Want to trigger earlier? Drop `trigger`. Want to pulse less? Increase `off`.

---

## 🔄 Cadence Algorithm

Each tick (1s):

1. Read all sensors
2. Bucket into zones
3. Calculate:

   * Thermal trend (slope over time)
   * Distance-to-trigger (how close are we?)
   * Fan RPM sanity
4. Recompute cadence: how long to stay in each mode
5. Log it. Live your life.

Cadence dynamically adapts to both temperature *and* the rate of change.

###  Smart Cadence Right? Yeah… But About That
Right now, we say we pulse for on/off durations… but actually, we recalculate cadence every tick - mid-pulse overrides always happen, breaking the spirit of duration-based control. 

The trend logic is solid — slope, proximity, all of it — but it’s being applied too frequently so the regression is not quite learning a trend. 
The right fix? Let the system commit and ride out the cadence, then reassess — just like a good feedback loop should. 

We’ll get there. For now? This works well enough, and I’ve got bigger dragons to slay. l33t!

---

## 🪓 Emergency Mode

If any zone hits 95°C for 3+ consecutive seconds, the daemon:

* Forces `performance` mode
* Logs critical status
* Waits for temps to drop before resuming

False positives avoided. Actual emergencies handled.

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
sudo journalctl -u autofan -f
```

---

## 🧪 Diagnostics

Tools to help:

```bash
sensors -j
watch -n1 "cat /sys/firmware/acpi/platform_profile"
journalctl -u autofan -f
```



Common tweaks:

| Symptom           | Fix                                            |
| ----------------- | ---------------------------------------------- |
| Too loud          | Lower `trigger`, raise `release`               |
| Too hot           | Raise `TREND_SENSITIVITY`, increase `on` time  |
| Toggles too often | Decrease `TREND_SENSITIVITY`, widen hysteresis |
| Nothing happens   | Check ACPI node exists and you have root       |


---

## 🙏 Acknowledgements

Created by **Nikunj Sura** (and sweat, and thermals, and maybe divine frustration). Co-developed and structured by **Deepseek** & **ChatGPT**, who finally stopped suggesting `i8kutils`.

This tool is part of a broader journey toward **SatyaNet** and **DharmaOS** — where systems serve truth, not just telemetry.

If you found this helpful, confusing, or delightfully hacky — you're welcome.

---

## ⚖️ License

MIT © 2025 Nikunj Sura — MIT. Use at your own risk. We’re just toggling a string in sysfs, not rewriting BIOS. Probably.

---