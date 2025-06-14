# Alienware m18 – Autofan Control Daemon

A lightweight Python daemon that keeps **all** fans on my Alienware m18 (and possibly other recent Dell laptops) spinning at safe, quiet speeds **without** direct access to the locked Dell SMM.
It does this by *pulsing* the ACPI platform‑profile between **`performance`** and **`balanced`** modes and by making zone‑aware decisions (CPU, GPU, memory, ambient).

---

## 1 · Why does this exist?

Dell / Alienware laptops hide their fan controller behind a proprietary SMM interface. On Linux you can *read* sensors (via `/sys/class/hwmon`) but you **can’t** set fan duty‑cycles directly. What **is** exposed, however, is the ACPI `platform_profile` ("Quiet", "Balanced", "Performance", …). When set to **`performance`**, Dell firmware ramps all fans to 80–100 %, while **`balanced`** eventually lets them idle at 0 RPM — often waiting until the CPU is well past 80 °C.

The daemon keeps temps low *and* noise down by:

1. **Reading** every temperature / fan sensor under `/sys/class/hwmon`
2. **Grouping** sensors into logical zones (CPU, GPU, memory, ambient)
3. **Pulsing** `platform_profile` to give short bursts of max airflow only when needed
4. **Applying hysteresis & per-zone thresholds** so it never chatters

---

**How we ended up here**

This project began in the BIOS trenches. I was on Arch at first — modding boot params, patching kernel modules, attempting EC access via `ectool`, `smm`, and a graveyard of ACPI hacks. Nothing stuck. Dell’s SMM stack is a fortress: EC writes are silently blocked, fan control is abstracted, and platform updates break what little access you might trick your way into.

Eventually I ditched the EC crusade, moved to Fedora for saner kernel defaults, and pivoted. Instead of fighting Dell’s firmware, *orchestrate* it. `platform_profile` became a lever. I noted setting the power profile to performance blasted the fans into a spaceship launch zone. It made me think why not just build a heuristic daemon to watch thermal zones and tap that lever — gently. The result is before you. No BIOS mods. No crashes. Just smarter thermals.

---

## 2 · Features

|                  | Description                                                                |
| ---------------- | -------------------------------------------------------------------------- |
| **Zone‑aware**   | Separate trigger / release temps & min RPM for CPU, GPU, memory & chassis. |
| **Soft pulses**  | User‑tunable ON/OFF cadence (different per severity level).                |
| **Failsafe**     | Locks to `performance` if any sensor ≥ **95 °C** or if the daemon crashes. |
| **Initial boost** | 12 s spin‑up when the daemon starts so idle fans never hit 0 RPM.          |
| **Rich logging** | One‑line status every 10 s + detailed debug when you need it.              |

---

## 3 · Requirements

* Linux with `/sys/firmware/acpi/platform_profile` support (Kernel ≥ 5.17).
* Python 3.8 +
* `root` privileges (needed to write to the ACPI node).

---

## 4 · Installation

```bash
# Clone & install deps
$ git clone https://github.com/yourname/alienware-autofan.git
$ cd alienware-autofan
$ python3 -m venv .venv && source .venv/bin/activate
$ pip install -r requirements.txt   # (only "prettytable" for now)
```

---

## 5 · Quick‑start

```bash
sudo ./autofan.py          # Ctrl‑C to stop
```

You should see log lines like:

```text
14:12:07 [INFO] State:NORMAL | Mode:balanced | Global:1 | cpu 58 °C/1200RPM | gpu 42 °C/0RPM | mem 46 °C/900RPM | amb 39 °C/700RPM
```

The first run also creates `/var/log/alienware_fancontrol.log`.

---

## 6 · Configuration

Open **`autofan.py`** and scroll to the **“Configuration”** block.  Every knob is right there:

* **Zone table** – regex patterns, trigger / release temps, min & max RPM.
* **Global constants** – `CRITICAL_TEMP`, `INITIAL_BOOST`, `CADENCE` pulse map …

> Tip Adjust *hysteresis* by keeping `release = trigger – ΔT` for each zone.

---

## 7 · Running as a service (systemd)

Create `/etc/systemd/system/autofan.service`:

```ini
[Unit]
Description=Alienware m18 Autofan Control
After=multi-user.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/autofan/autofan.py
WorkingDirectory=/opt/autofan
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now autofan.service
sudo journalctl -u autofan.service -f   # live logs
```

---

## 8 · Troubleshooting

| Problem                                                               | Fix                                                                                                 |
| --------------------------------------------------------------------- |-----------------------------------------------------------------------------------------------------|
| `Error: Must run as root`                                             | Start with `sudo` or via the systemd unit above.                                                    |
| `OSError: [Errno 22] Invalid argument` reading `/sys/class/hwmon/...` | Harmless – some Dell nodes reject reads; the daemon already ignores them.                           |
| Fans never ramp                                                       | Check that `platform_profile` really toggles between `balanced` & `performance` @ACPI_PROFILE_PATH |

    This program has been tested on my personal laptop only. You may need to tweak it for you particular use case. 
    Just run the fan_monitor.py and temp_monitor.py to run a bit of debugging on your particular hardware and configure the zones as per your device.

---

## 9 · Contributing

1. Fork → branch → PR.
2. Keep the style in `black`/`isort` format.
3. Add a line to `CHANGELOG.md` (new or fixed, no matter how small!).

---

## 10 · License

MIT © 2025 Nikunj Sura  —  *Use at your own risk; high‑speed fans can be noisy!*
