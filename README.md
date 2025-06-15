# Alienware m18 – Adaptive Fan Whisperer (v2)

Welcome to the unofficial thermal rebellion for Dell’s Alienware m18 and friends.
This is not a flashy RGB controller. 
This is a disciplined little Python daemon that outsmarts proprietary firmware using the only lever Dell left unlocked:
**`/sys/firmware/acpi/platform_profile`**.

You can’t set fan RPMs directly on these machines (thanks SMM lockout), but you can *nudge* the firmware into doing your bidding. 
This tool pulses the “performance” profile at just the right moments to keep your system cool without sounding like a leaf blower.

Crafted by a human who actually lives with this laptop, and co-developed by an AI who’s learned to speak both ACPI and sarcasm.

---

## 🧭 Why This Exists

Dell hides fan control behind a proprietary System Management Mode (SMM) interface. 
On Linux, you can read sensors just fine. 
But writing fan speeds? Nope. Kernel modules? Blocked. EC access? Laughed out of the room. SMBIOS? Dead end.

What’s left is a single ACPI control node:

* `platform_profile = performance` → fans ramp hard (\~80–100 %)
* `platform_profile = balanced` → fans eventually idle (but often when its about to melt)

This daemon *flips that switch on and off intelligently*, based on:

* Real sensor data
* Calculated temperature trends (with timestamps)
* Custom zones extrapolated from the `hwmon` (CPU/GPU/ambient/memory)
* Hysteresis and cooldown logic
* Emergency debounce for real spikes (not flukes [TODO] there is an improvement to be made here])

---

## 🛠️ Features

| Feature                      | Why It Matters                                           |
| ---------------------------- |----------------------------------------------------------|
| **ACPI Pulse Control**       | Uses Dell’s own knobs against it                         |
| **Thermal Zones**            | Separates CPU/GPU/Memory/Ambient with per-zone tuning    |
| **Trend-Aware Cadence**      | Doesn’t just react to heat — it predicts it              |
| **Emergency Debounce**       | Prevents panic toggles on one-off spikes                 |
| **Global Fan Sanity Check**  | Detects stalled fans or sensors                          |
| **Hysteresis, Not Hysteria** | Avoids flapping between states                           |
| **Zero Dependencies**        | Pure Python, no `i8k` hacks, no kernel voodoo, just acpi |

---

## 🧬 Design Philosophy

> Don't fight it, just go with it.

After days wasted trying to reverse-engineer Dell’s Embedded Controller (EC), trying to patch `i8k`, 
and spelunking through ACPI tables and BIOS hell... the real insight came:

Performance mode causes full the fan to blast into orbit. Works but will make a baby cry. 
Pulse it and we have a crutch for the BIOS's thermal amnesia - and! my wife doesn’t think we’re leaving the atmosphere.

This daemon reads every thermal sensor on `/sys/class/hwmon`, buckets them by zone, 
and builds a real-time heuristic of how fast temps are rising and how close we are to the danger zone. 
Then it toggles profiles in a slightly dumb way - more on this in the Cadence Algorithm.

You could say it’s fan control by proxy - **My Satyanet meets firmware aikido**.

---

## 📦 Requirements

* Linux kernel **5.17+** (or so says ChatGPT)
* Python **3.8+** (I tested with 3.13.3)
* Root access to write to ACPI node. [TODO] Space to improve without root perhaps. 
* A laptop that supports `/sys/firmware/acpi/platform_profile` (Alienware m18 confirmed)

---

## 🚀 Quickstart

```bash
git clone https://github.com/nikunj-sura/alienware-autofan.git # Or your repo URL
cd alienware-autofan
sudo python3 src/autofan.py
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

You can fine-tune thresholds per zone. Want memory to chill harder? Drop its trigger temp. Want less fan blasting? Increase the `release`.

Other Tune-able parameters:

```python
ACPI_PROFILE_PATH = "/sys/firmware/acpi/platform_profile"
LOG_FILE = "/var/log/alienware_fancontrol.log"
POLL_INTERVAL = 1  # seconds between sensor sweeps
LOG_INTERVAL = 10  # seconds between log lines
INITIAL_BOOST = 5  # seconds – keep profile=performance after boot
CRITICAL_TEMP = 95  # °C – emergency profile lock
HISTORY_WINDOW = 30  # seconds for moving average calculation
TREND_SENSITIVITY = 0.3  # how aggressively we adjust cadence (0-1)
EMERGENCY_DEBOUNCE = 3  # consecutive seconds above critical temp

# See next section to learn why this is unimportant for now
BASE_CADENCE = {
    0: {"on": 0, "off": 9999},  # Cool zone
    1: {"on": 1, "off": 14},    # Warm zone
    2: {"on": 4, "off": 8},     # Hot zone
}

# Zone importance weights for trend calculation
ZONE_WEIGHTS = {"cpu": 1.0, "gpu": 1.0, "memory": 0.6, "ambient": 0.4}
```

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
Right now, we say we pulse for on/off durations… but actually, we recalculate cadence every tick -
mid-pulse overrides always happen, breaking the spirit of duration-based control. 

The trend logic is solid — slope, proximity, all of it — the regression is learning a trend but it’s overridden by the base interval too frequently.

[TODO] Let the system commit and ride out the cadence, then reassess. The general time to ramp fan rpm to max is about 10s, and drop to 0 happens in about 15. We could use these bounds to come up with a better pulser (is that a word?)

We’ll get there. For now? For now, this works well enough, and I’ve got bigger dragons to slay. l33t code, here I come!!

---

## 🪓 Emergency Mode

If any zone hits 95°C for 3+ consecutive seconds, the daemon:

* Forces `performance` mode
* Logs critical status
* Waits for temps to drop before resuming

False positives avoided. Actual emergencies handled.
ish
[TOOD] we do hit the crit temp. Currently the monitors read a temp for every CPU. I suspect when a CPU gets a high load, it spikes temp before its balanced. This micro spike on the sensor can come through. 
Right now we are taking max but perhaps this is where we want moving avg or a smarter aggregator.
---

## 🔁 Systemd Setup

Save this as `/etc/systemd/system/autofan.service`:

```ini
[Unit]
Description=Alienware Adaptive Fan Control
After=multi-user.target

[Service]
Type=simple
# Adjust ExecStart to the correct path of your cloned repository
ExecStart=/usr/bin/python3 /path/to/your/cloned/m18-fancontrol/src/autofan.py
# Example: ExecStart=/usr/bin/python3 /opt/m18-fancontrol/src/autofan.py
# You can also add --config /path/to/config.yaml if needed
Restart=always
RestartSec=1
User=root
# Consider setting WorkingDirectory if your script relies on relative paths for non-config files
# WorkingDirectory=/path/to/your/cloned/m18-fancontrol/src/

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

Tools to help:

```bash
watch sensors 
watch -n1 "cat /sys/firmware/acpi/platform_profile"
journalctl -u autofan -f
# if you want more tools to port to your device - reach out. I push my custom temperature and fan monitors that started this whole shebang 
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

Created with sweat, blasting fans, melting laptops, and maybe divine will. Co-developed and structured by **Deepseek** who wrote a lot of code & **ChatGPT**, who finally stopped suggesting `i8kutils` and perhaps a little guidance from an ADHD human.

This tool is part of a broader journey toward completing my **SatyaNet** projects.

`SatyaNet` like skynet but serves Truth instead of the AI overloads

---

## ⚖️ License

MIT © 2025 Nikunj Sura — MIT. Use at your own risk. We’re just toggling a string in sysfs, not rewriting BIOS. Probably.

---