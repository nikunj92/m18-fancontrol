#!/usr/bin/env python3
"""Autofan Control Daemon

This service toggles the Dell/Alienware **ACPI performance profile** to keep all
fans spinning while respecting per‑zone temperature targets.  Because the Alienware
M18 does **not** expose a writable SMM interface, we can only pulse between the
`performance` and `balanced` ACPI profiles and rely on the firmware tables to do
the actual PWM ramp‑up / ramp‑down.

The daemon groups sensors and fans into logical *zones* (CPU, GPU, Ambient,
Memory).  Every second it

1.  Reads **temp** and **fan** sensors under */sys/class/hwmon*.
2.  Maps each sensor to a zone via regex rules.
3.  Calculates a *severity* level per zone and globally.
4.  Uses a tiny state‑machine to decide whether to write
    `performance` or `balanced` to
    */sys/firmware/acpi/platform_profile*.

Highlights
~~~~~~~~~~
*   Per‑zone hysteresis → avoids thrashing profiles.
*   Global emergency clamp at °C 95.
*   Config is a single table at the top for quick tweaking or YAML import.
*   Designed to run as **root** with `systemd` (example unit in README).
"""
from __future__ import annotations

import glob
import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple

###############################################################################
# Configuration
###############################################################################

ACPI_PROFILE_PATH = "/sys/firmware/acpi/platform_profile"
LOG_FILE          = "/var/log/alienware_fancontrol.log"
POLL_INTERVAL     = 1          # seconds between sensor sweeps
LOG_INTERVAL      = 10         # seconds between log lines
INITIAL_BOOST     = 12         # seconds – keep profile=performance after boot
CRITICAL_TEMP     = 95         # °C – emergency profile lock

# How long to keep profile=performance/balanced at each global severity level.
# Off sec ~= how long we stay in balanced, On sec ~= performance pulse length.
CADENCE: Dict[int, Dict[str, int]] = {
    0: {"on": 0, "off": 9999},    # Cool – stay balanced indefinitely.
    1: {"on": 1, "off": 14},      # Warm – short pulses every ~15 s.
    2: {"on": 4, "off": 8},       # Hot  – longer pulses, shorter rests.
}

@dataclass(frozen=True)
class Zone:
    """Sensor/fan grouping rules and thresholds for a thermal zone."""

    name:        str
    temp_regex:  List[str]
    fan_regex:   List[str]
    trigger:     int   # °C above which zone is considered *hot*
    release:     int   # °C below which zone is considered *cool*
    min_rpm:     int   # treat fan RPM below this as *too low*
    max_rpm:     int   # observed maximum – only for logging/analysis

    @property
    def high_rpm(self) -> int:   # 85% of max RPM
        return int(self.max_rpm * 0.85)

# One‑stop table for easy tweaking ------------------------------------------------
ZONES: Tuple[Zone, ...] = (
    Zone(
        name="cpu",
        temp_regex=[r"package", r"core", r"cpu"],
        fan_regex=[r"fan1", r"cpu"],
        trigger=75,
        release=68,
        min_rpm=300,
        max_rpm=4200,
    ),
    Zone(
        name="gpu",
        temp_regex=[r"video", r"gpu"],
        fan_regex=[r"fan2", r"gpu"],
        trigger=70,
        release=62,
        min_rpm=300,
        max_rpm=3900,
    ),
    Zone(
        name="ambient",
        temp_regex=[r"ambient", r"iwlwifi"],
        fan_regex=[r"fan3", r"chassis"],
        trigger=51,
        release=48,
        min_rpm=300,
        max_rpm=8900,
    ),
    Zone(
        name="memory",
        temp_regex=[r"sodimm", r"dimm", r"nvme"],
        fan_regex=[r"fan4", r"memory"],
        trigger=65,
        release=58,
        min_rpm=300,
        max_rpm=6700,
    ),
)

###############################################################################
# Helper functions
###############################################################################

def _matches_any(regexes: List[str], text: str) -> bool:
    """Case‑insensitive search of *text* against any regex pattern."""
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in regexes)


def read_hwmon() -> Tuple[Dict[str, float], Dict[str, int]]:
    """Gather temperatures (°C) and fan RPMs, grouped by zone name.

    Returns
    -------
    temps : dict[str, float]
        Maximum temperature per zone.
    fans  : dict[str, int]
        Maximum observed RPM per zone (0 if no fan found).
    """
    temps: Dict[str, List[float]] = defaultdict(list)
    fans:  Dict[str, List[int]]   = defaultdict(list)

    for hwmon_path in glob.glob("/sys/class/hwmon/hwmon*"):
        if not os.path.isdir(hwmon_path):
            continue  # Folder may disappear after suspend/resume.
        try:
            with open(os.path.join(hwmon_path, "name")) as f:
                device = f.read().strip()
        except FileNotFoundError:
            continue  # The hwmon vanished mid‑loop.

        for entry in os.listdir(hwmon_path):
            sensor_path = os.path.join(hwmon_path, entry)

            # ---- Temperature sensors ---------------------------------------
            if entry.startswith("temp") and entry.endswith("_input"):
                try:
                    with open(sensor_path) as f:
                        value = int(f.read().strip()) / 1000.0  # millideg → °C
                except (OSError, ValueError):
                    continue

                # Optional label file → friendlier matching strings
                label = None
                label_path = sensor_path.replace("_input", "_label")
                if os.path.exists(label_path):
                    try:
                        with open(label_path) as f:
                            label = f.read().strip()
                    except OSError:
                        pass

                key = f"{device}:{label}" if label else f"{device}:{entry}"

                for zone in ZONES:
                    if _matches_any(zone.temp_regex, key):
                        temps[zone.name].append(value)
                        break

            # ---- Fan sensors ---------------------------------------------
            elif entry.startswith("fan") and entry.endswith("_input"):
                try:
                    with open(sensor_path) as f:
                        rpm = int(f.read().strip())
                except (OSError, ValueError):
                    continue
                if rpm == 0:
                    continue

                label = None
                label_path = sensor_path.replace("_input", "_label")
                if os.path.exists(label_path):
                    try:
                        with open(label_path) as f:
                            label = f.read().strip()
                    except OSError:
                        pass
                key = f"{device}:{label}" if label else f"{device}:{entry}"

                for zone in ZONES:
                    if _matches_any(zone.fan_regex, key):
                        fans[zone.name].append(rpm)
                        break

    # Collapse lists → single value per zone (max temp, max rpm) -------------
    agg_temps = {z: max(v) for z, v in temps.items()}
    agg_fans  = {z: max(v) for z, v in fans.items()}
    return agg_temps, agg_fans


def set_acpi_profile(profile: str) -> None:
    """Write *profile* to the ACPI platform profile sysfs node."""
    try:
        with open(ACPI_PROFILE_PATH, "w") as f:
            f.write(profile)
        logging.debug("ACPI profile → %s", profile)
    except OSError as exc:
        logging.error("Unable to set ACPI profile: %s", exc)

###############################################################################
# Core state‑machine
###############################################################################

class PulseController:
    """Maintain a global ACPI profile based on per‑zone readings."""

    def __init__(self) -> None:
        self._state = "INITIAL_BOOST"
        self._profile = "performance"  # what we *believe* firmware is running
        self._last_switch = time.time()
        self._emergency = False

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------
    def tick(self, temps: Dict[str, float], fans: Dict[str, int]) -> None:
        """Advance the controller one step."""
        now = time.time()
        elapsed = now - self._last_switch

        # ---------- Emergency clamp --------------------------------------
        if temps and max(temps.values()) >= CRITICAL_TEMP:
            if not self._emergency:
                logging.critical("Temperature ≥ %d °C — locking performance", CRITICAL_TEMP)
                self._force_profile("performance")
                self._emergency = True
            return  # nothing else matters in emergency state

        # ---------- Determine global severity ---------------------------
        global_sev = max(_zone_severity(z, temps.get(z.name, 0)) for z in ZONES)
        pulse = CADENCE[global_sev]

        # ---------- State machine ---------------------------------------
        if self._state == "INITIAL_BOOST":
            if elapsed >= INITIAL_BOOST:
                self._switch_to("balanced")
                self._state = "NORMAL"

        elif self._state == "NORMAL":
            if self._profile == "performance":
                # Time to relax?
                if elapsed >= pulse["on"]:
                    self._switch_to("balanced")
            else:  # balanced
                # Need to boost?
                if elapsed >= pulse["off"] or _fan_too_low(fans):
                    self._switch_to("performance")

        # ---------- Periodic log every 10 s -----------------------------
        if int(now) % LOG_INTERVAL == 0:
            line = [f"{z.name}:{temps.get(z.name,0):.1f}°C/{fans.get(z.name,0)}rpm" for z in ZONES]
            logging.info("prof=%s sev=%d %s", self._profile[:4], global_sev, " ".join(line))

    # ------------------------------------------------------------------
    # Internals helpers
    # ------------------------------------------------------------------
    def _switch_to(self, profile: str) -> None:
        if self._profile != profile:
            self._profile = profile
            self._last_switch = time.time()
            set_acpi_profile(profile)

    def _force_profile(self, profile: str) -> None:
        self._profile = profile
        self._last_switch = time.time()
        set_acpi_profile(profile)

###############################################################################
# Utility helpers
###############################################################################

def _zone_severity(zone: Zone, temp: float) -> int:
    """Return 0/1/2 depending on *temp* relative to zone thresholds."""
    if temp >= zone.trigger:
        return 2
    if temp >= zone.release:
        return 1
    return 0


def _fan_too_low(fans: Dict[str, int]) -> bool:
    """True if any zone fan RPM is below its configured *min_rpm*."""
    for zone in ZONES:
        if fans.get(zone.name, zone.min_rpm) < zone.min_rpm:
            return True
    return False

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S',
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler()
        ]
    )
    logging.info("Starting Heuristic Curved Fan Control")

###############################################################################
# Main entry point
###############################################################################

def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("[ERR] Must be run as root.")

    setup_logging()
    logging.info("Boot: forcing performance for %d s", INITIAL_BOOST)

    controller = PulseController()
    set_acpi_profile("performance")

    try:
        while True:
            temps, fans = read_hwmon()
            controller.tick(temps, fans)
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logging.info("Interrupted → balanced & exit")
        set_acpi_profile("balanced")
    except Exception:
        logging.exception("Fatal error — locking performance and exiting")
        set_acpi_profile("performance")
        raise

if __name__ == "__main__":
    main()
