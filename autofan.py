#!/usr/bin/env python3
"""Autofan Control Daemon – Minimal, Deterministic State Machine

- Instant profile switching on severity logic.
- Emergency overheat lock.
- Initial performance boost at boot.
- Per-zone min RPM enforcement.
"""

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
LOG_FILE = "/var/log/alienware_fancontrol.log"
OFF = "balanced"          # ACPI flags for my machine
ON = "performance"
POLL_INTERVAL = 1         # seconds between sensor sweeps
LOG_INTERVAL = 10         # seconds between log lines
INITIAL_BOOST = 5         # seconds at boot in performance mode
CRITICAL_TEMP = 90        # °C – emergency lock
EMERGENCY_DEBOUNCE = 5    # consecutive seconds above critical temp

@dataclass(frozen=True)
class ZoneConfig:
    """Configuration for a thermal zone."""
    name: str
    temp_regex: List[str]
    fan_regex: List[str]
    trigger: int    # °C – hot threshold
    release: int    # °C – warm threshold
    min_rpm: int    # Minimum RPM for this zone's fan
    max_rpm: int    # Observed max – only for logging

ZONES: Tuple[ZoneConfig, ...] = (
    ZoneConfig(
        name="cpu",
        temp_regex=[r"package", r"core", r"cpu"],
        fan_regex=[r"fan1", r"cpu"],
        trigger=75,
        release=68,
        min_rpm=300,
        max_rpm=4200,
    ),
    ZoneConfig(
        name="gpu",
        temp_regex=[r"video", r"gpu"],
        fan_regex=[r"fan2", r"gpu"],
        trigger=70,
        release=62,
        min_rpm=300,
        max_rpm=3900,
    ),
    ZoneConfig(
        name="ambient",
        temp_regex=[r"ambient", r"iwlwifi"],
        fan_regex=[r"fan3", r"chassis"],
        trigger=58,
        release=51,
        min_rpm=300,
        max_rpm=8900,
    ),
    ZoneConfig(
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
# Sensor Reader
###############################################################################

class SensorReader:
    """Reads temperatures and fan RPMs per zone from /sys/class/hwmon."""

    @staticmethod
    def _matches_any(regexes: List[str], text: str) -> bool:
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in regexes)

    @staticmethod
    def read() -> Tuple[Dict[str, float], Dict[str, int]]:
        temps: Dict[str, List[float]] = defaultdict(list)
        fans: Dict[str, List[int]] = defaultdict(list)
        for hwmon_path in glob.glob("/sys/class/hwmon/hwmon*"):
            if not os.path.isdir(hwmon_path):
                continue
            try:
                with open(os.path.join(hwmon_path, "name")) as f:
                    device = f.read().strip()
            except FileNotFoundError:
                continue
            for entry in os.listdir(hwmon_path):
                sensor_path = os.path.join(hwmon_path, entry)
                if entry.startswith("temp") and entry.endswith("_input"):
                    SensorReader._process_sensor(entry, sensor_path, device, temps, True)
                elif entry.startswith("fan") and entry.endswith("_input"):
                    SensorReader._process_sensor(entry, sensor_path, device, fans, False)
        agg_temps = {z: max(v) for z, v in temps.items() if v}
        agg_fans = {z: max(v) for z, v in fans.items() if v}
        return agg_temps, agg_fans

    @staticmethod
    def _process_sensor(entry, sensor_path, device, data, is_temp):
        try:
            with open(sensor_path) as f:
                value = int(f.read().strip()) / (1000.0 if is_temp else 1)
        except (OSError, ValueError):
            return
        label_path = sensor_path.replace("_input", "_label")
        label = None
        if os.path.exists(label_path):
            try:
                with open(label_path) as f:
                    label = f.read().strip()
            except OSError:
                pass
        key = f"{device}:{label}" if label else f"{device}:{entry}"
        for zone in ZONES:
            regexes = zone.temp_regex if is_temp else zone.fan_regex
            if SensorReader._matches_any(regexes, key):
                data[zone.name].append(value)
                break

###############################################################################
# State Machine Controller
###############################################################################

class FanControl:
    """Deterministic, minimal state machine for thermal/fan control."""

    def __init__(self):
        self.state = "INITIAL_BOOST"
        self.current_profile = None
        self.last_switch = time.time()
        self.emergency = False
        self.over_counter = 0
        self.last_log = 0

    @staticmethod
    def zone_severity(zone: ZoneConfig, temp: float) -> int:
        """0=cool, 1=warm, 2=hot."""
        if temp >= zone.trigger:
            return 2
        if temp >= zone.release:
            return 1
        return 0

    def tick(self, temps: Dict[str, float], fans: Dict[str, int]):
        now = time.time()
        elapsed = now - self.last_switch
        self.current_profile = get_acpi_profile()

        if self.emergency:
            if self.current_profile == OFF:
                self.emergency = False
            else:
                return  # locked in performance

        # Emergency: handle critical temperature lock with debounce
        self.handle_emergency(temps)

        # --- Minimal deterministic state logic ---
        # Highest severity across all zones
        max_sev = 0
        sev_by_zone = {}
        for zone in ZONES:
            temp = temps.get(zone.name, 0)
            sev = self.zone_severity(zone, temp)
            sev_by_zone[zone.name] = sev
            if sev > max_sev:
                max_sev = sev

        # 1. Any zone is HOT (sev=2): set performance
        if max_sev == 2:
            self.set_profile(ON)
        # 2. Any zone is WARM (sev=1) and its fan below min: set performance
        elif max_sev == 1 and any(
            sev_by_zone[zone.name] == 1 and fans.get(zone.name, 0) < zone.min_rpm
            for zone in ZONES
        ):
            self.set_profile(ON)
        # 3. Otherwise: stay or return to balanced
        else:
            self.set_profile(OFF)

        self.log_status(now, temps, fans, sev_by_zone, max_sev)

    def handle_emergency(self, temps: Dict[str, float]):
        """Lock to performance if any temp exceeds CRITICAL_TEMP for EMERGENCY_DEBOUNCE consecutive ticks."""
        if any(temp >= CRITICAL_TEMP for temp in temps.values()):
            self.over_counter += 1
        else:
            self.over_counter = 0
        if self.over_counter >= EMERGENCY_DEBOUNCE:
            logging.critical("EMERGENCY: Temp >= %d°C for %ds – locking performance.", CRITICAL_TEMP, EMERGENCY_DEBOUNCE)
            self.set_profile("performance", force=True)
            self.emergency = True
        # to release emergency - user needs to reset the acpi.

    def set_profile(self, profile: str, force: bool = False):
        if force or self.current_profile != profile:
            self.last_switch = time.time()
            set_acpi_profile(profile)

    def log_status(self, now, temps, fans, sev_by_zone, max_sev):
        """Log system status periodically."""
        if now - self.last_log < LOG_INTERVAL:
            return
        self.last_log = now
        status_lines = []
        for zone in ZONES:
            t = temps.get(zone.name, 0.0)
            rpm = fans.get(zone.name, 0)
            sev = sev_by_zone[zone.name]
            status_lines.append(f"{zone.name}:{t:.1f}°C/{rpm}rpm(sev={sev})")
        logging.info(
            "prof=%s sev=%d %s",
            self.current_profile[:4], max_sev, " ".join(status_lines)
        )

###############################################################################
# System Functions
###############################################################################

def set_acpi_profile(profile: str) -> None:
    try:
        with open(ACPI_PROFILE_PATH, "w") as f:
            f.write(profile)
        logging.debug("ACPI profile → %s", profile)
    except OSError as exc:
        logging.error("Unable to set ACPI profile: %s", exc)

def get_acpi_profile() -> str:
    """Read the current ACPI platform profile."""
    try:
        with open(ACPI_PROFILE_PATH, "r") as f:
            return f.read().strip()
    except OSError:
        return ""

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
    logging.info("Starting Minimal Deterministic Fan Control")

###############################################################################
# Main Entry Point
###############################################################################

def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("[ERR] Must be run as root.")

    setup_logging()
    logging.info("Boot: forcing performance for %d s", INITIAL_BOOST)
    controller = FanControl()
    set_acpi_profile("performance")
    start_time = time.time()

    try:
        while True:
            temps, fans = SensorReader.read()
            # Remain in performance for INITIAL_BOOST after boot
            if time.time() - start_time < INITIAL_BOOST:
                controller.set_profile("performance")
            else:
                if controller.state == "INITIAL_BOOST":
                    controller.state = "NORMAL"
                controller.tick(temps, fans)
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logging.info("Interrupted – setting balanced profile & exit")
        set_acpi_profile("balanced")
    except Exception:
        logging.exception("Fatal error – locking performance and exiting")
        set_acpi_profile("performance")
        raise

if __name__ == "__main__":
    main()
