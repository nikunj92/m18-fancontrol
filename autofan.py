#!/usr/bin/env python3
"""Autofan Control Daemon with Enhanced Adaptive Cadence

Improved version with:
- Time-corrected linear regression
- Weighted zone trends
- Cadence recomputation each tick
- Distance-to-trigger hotness factor
- Global fan RPM monitoring
- Debounced emergency handling
"""

import glob
import logging
import os
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, List, Tuple, Deque

###############################################################################
# Configuration
###############################################################################

ACPI_PROFILE_PATH = "/sys/firmware/acpi/platform_profile"
LOG_FILE = "/var/log/alienware_fancontrol.log"
POLL_INTERVAL = 1  # seconds between sensor sweeps
LOG_INTERVAL = 10  # seconds between log lines
INITIAL_BOOST = 5  # seconds – keep profile=performance after boot
CRITICAL_TEMP = 95  # °C – emergency profile lock
HISTORY_WINDOW = 30  # seconds for moving average calculation
TREND_SENSITIVITY = 0.3  # how aggressively we adjust cadence (0-1)
EMERGENCY_DEBOUNCE = 3  # consecutive seconds above critical temp

# Base cadence - these values will be adjusted dynamically
BASE_CADENCE: Dict[int, Dict[str, int]] = {
    0: {"on": 0, "off": 9999},  # Cool – stay balanced indefinitely
    1: {"on": 1, "off": 30},  # Warm – short pulses every ~15 s
    2: {"on": 4, "off": 12},  # Hot  – longer pulses, shorter rests
}

# Zone importance weights for trend calculation
ZONE_WEIGHTS = {"cpu": 1.0, "gpu": 1.0, "memory": 0.6, "ambient": 0.4}


@dataclass(frozen=True)
class ZoneConfig:
    """Configuration for a thermal zone."""
    name: str
    temp_regex: List[str]
    fan_regex: List[str]
    trigger: int  # °C above which zone is considered hot
    release: int  # °C below which zone is considered cool
    min_rpm: int  # treat fan RPM below this as too low
    max_rpm: int  # observed maximum – only for logging/analysis

    @property
    def high_rpm(self) -> int:
        """85% of max RPM for logging/analysis"""
        return int(self.max_rpm * 0.85)


# Zone configuration table
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
    """Reads temperature and fan data from hardware sensors"""

    @staticmethod
    def _matches_any(regexes: List[str], text: str) -> bool:
        """Case-insensitive search against regex patterns"""
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in regexes)

    @staticmethod
    def read() -> Tuple[Dict[str, float], Dict[str, int]]:
        """
        Gather temperatures (°C) and fan RPMs from /sys/class/hwmon

        Returns:
            temps: Maximum temperature per zone
            fans: Maximum RPM per zone (0 if no fan found)
        """
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
                SensorReader._process_sensor(entry, sensor_path, device, temps, fans)

        # Collapse lists to single value per zone
        agg_temps = {z: max(v) for z, v in temps.items() if v}
        agg_fans = {z: max(v) for z, v in fans.items() if v}
        return agg_temps, agg_fans

    @classmethod
    def _process_sensor(cls, entry: str, sensor_path: str, device: str,
                        temps: Dict[str, List[float]],
                        fans: Dict[str, List[int]]):
        """Process individual sensor entries"""
        # Temperature sensors
        if entry.startswith("temp") and entry.endswith("_input"):
            cls._process_temp_sensor(entry, sensor_path, device, temps)
        # Fan sensors
        elif entry.startswith("fan") and entry.endswith("_input"):
            cls._process_fan_sensor(entry, sensor_path, device, fans)

    @classmethod
    def _process_temp_sensor(cls, entry: str, sensor_path: str, device: str,
                             temps: Dict[str, List[float]]):
        """Process temperature sensor input"""
        try:
            with open(sensor_path) as f:
                value = int(f.read().strip()) / 1000.0  # millideg → °C
        except (OSError, ValueError):
            return

        # Get sensor label if available
        label_path = sensor_path.replace("_input", "_label")
        label = None
        if os.path.exists(label_path):
            try:
                with open(label_path) as f:
                    label = f.read().strip()
            except OSError:
                pass

        key = f"{device}:{label}" if label else f"{device}:{entry}"
        cls._assign_to_zone(key, value, temps, is_temp=True)

    @classmethod
    def _process_fan_sensor(cls, entry: str, sensor_path: str, device: str,
                            fans: Dict[str, List[int]]):
        """Process fan sensor input"""
        try:
            with open(sensor_path) as f:
                rpm = int(f.read().strip())
        except (OSError, ValueError):
            return

        if rpm == 0:
            return

        # Get fan label if available
        label_path = sensor_path.replace("_input", "_label")
        label = None
        if os.path.exists(label_path):
            try:
                with open(label_path) as f:
                    label = f.read().strip()
            except OSError:
                pass

        key = f"{device}:{label}" if label else f"{device}:{entry}"
        cls._assign_to_zone(key, rpm, fans, is_temp=False)

    @classmethod
    def _assign_to_zone(cls, key: str, value: float,
                        data: Dict[str, List[float]], is_temp: bool):
        """Assign sensor reading to appropriate zone"""
        for zone in ZONES:
            regex_list = zone.temp_regex if is_temp else zone.fan_regex
            if cls._matches_any(regex_list, key):
                data[zone.name].append(value)
                break


###############################################################################
# Thermal Analysis
###############################################################################

class ThermalAnalyzer:
    """Analyzes thermal data and calculates trends with time correction"""

    def __init__(self):
        # Store (timestamp, temperature) pairs for accurate regression
        self.temp_history: Dict[str, Deque[Tuple[float, float]]] = {
            zone.name: deque(maxlen=HISTORY_WINDOW) for zone in ZONES
        }

    def update_history(self, temps: Dict[str, float]):
        """Update temperature history with current timestamp"""
        now = time.time()
        for zone in ZONES:
            if temp := temps.get(zone.name):
                self.temp_history[zone.name].append((now, temp))

    def calculate_trend(self, zone_name: str) -> float:
        """
        Calculate temperature trend for a zone (-1 to 1)
        Uses actual timestamps for accurate regression
        """
        history = self.temp_history[zone_name]
        if len(history) < 2:
            return 0.0

        # Convert to (x, y) with x = seconds since first sample
        first_ts = history[0][0]
        points = [(ts - first_ts, temp) for ts, temp in history]
        x, y = zip(*points)

        # Perform linear regression
        n = len(x)
        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(xi * yi for xi, yi in zip(x, y))
        sum_x2 = sum(xi ** 2 for xi in x)

        try:
            slope = (n * sum_xy - sum_x * sum_y) / (n * sum_x2 - sum_x ** 2)
        except ZeroDivisionError:
            return 0.0

        # Find matching zone configuration
        zone = next((z for z in ZONES if z.name == zone_name), None)
        if not zone:
            return 0.0

        # Normalize slope to range -1 to 1 based on zone thresholds
        threshold_range = zone.trigger - zone.release
        if threshold_range <= 0:
            return 0.0

        return max(-1.0, min(1.0, slope / threshold_range))

    @staticmethod
    def zone_severity(zone: ZoneConfig, temp: float) -> int:
        """Determine thermal severity level (0-2) for a zone"""
        if temp >= zone.trigger:
            return 2
        if temp >= zone.release:
            return 1
        return 0

    @staticmethod
    def fans_too_low(fans: Dict[str, int]) -> bool:
        """Check if any fan is below minimum RPM (global check)"""
        if not fans:
            return False
        global_min = min(fans.values())
        return global_min < min(zone.min_rpm for zone in ZONES)


###############################################################################
# Pulse Controller
###############################################################################

class PulseController:
    """Manages ACPI profile switching with enhanced adaptive cadence"""

    def __init__(self, analyzer: ThermalAnalyzer):
        self.state = "INITIAL_BOOST"
        self.current_profile = "performance"
        self.last_switch = time.time()
        self.emergency = False
        self.analyzer = analyzer
        self.last_log = 0
        self.last_global_severity = 0
        self.over_counter = 0  # For emergency debounce

    def tick(self, temps: Dict[str, float], fans: Dict[str, int]):
        """Advance the controller state"""
        now = time.time()
        elapsed = now - self.last_switch

        # Update temperature history
        self.analyzer.update_history(temps)

        # Emergency temperature handling with debounce
        self.handle_emergency(temps)

        if self.emergency:
            return  # Stay locked in performance mode

        # Calculate global severity
        global_sev = self.calculate_global_severity(temps)
        self.last_global_severity = global_sev

        # Compute cadence dynamically
        trend = self._max_weighted_trend(temps)
        global_hot = self._compute_global_hot(temps)
        pulse_cfg = self._cadence_for(global_sev, trend, global_hot)

        # State machine logic
        self.handle_state_transition(now, elapsed, pulse_cfg, fans)

        # Periodic logging
        self.log_status(now, temps, fans, global_sev, pulse_cfg)

    def handle_emergency(self, temps: Dict[str, float]):
        """Handle critical temperature situation with debounce"""
        critical = False
        for temp in temps.values():
            if temp >= CRITICAL_TEMP:
                critical = True
                break

        if critical:
            self.over_counter += 1
        else:
            self.over_counter = 0

        if self.over_counter >= EMERGENCY_DEBOUNCE:
            if not self.emergency:
                logging.critical("Temperature ≥ %d °C for %ds — locking performance",
                                 CRITICAL_TEMP, EMERGENCY_DEBOUNCE)
                self.set_profile("performance", force=True)
                self.emergency = True
        elif self.emergency and self.over_counter == 0:
            # Return from emergency if temps normalized
            self.emergency = False
            logging.warning("Temperature normalized, exiting emergency mode")

    def calculate_global_severity(self, temps: Dict[str, float]) -> int:
        """Determine overall system thermal severity"""
        max_severity = 0
        for zone in ZONES:
            temp = temps.get(zone.name, 0)
            severity = self.analyzer.zone_severity(zone, temp)
            if severity > max_severity:
                max_severity = severity
        return max_severity

    def _max_weighted_trend(self, temps: Dict[str, float]) -> float:
        """Calculate maximum weighted trend across zones"""
        trends = []
        for zone in ZONES:
            if zone.name in temps:
                trend_val = self.analyzer.calculate_trend(zone.name)
                weight = ZONE_WEIGHTS.get(zone.name, 0.5)
                trends.append(weight * trend_val)
        return max(trends) if trends else 0.0

    def _compute_global_hot(self, temps: Dict[str, float]) -> float:
        """Compute global hotness factor based on distance to triggers"""
        global_hot = 0.0
        for zone in ZONES:
            temp = temps.get(zone.name, 0)
            if temp < zone.release:
                hotness = 0.0
            else:
                # Normalize to [0,1] between release and trigger
                span = zone.trigger - zone.release
                if span <= 0:
                    hotness = 0.0
                else:
                    hotness = min(1.0, (temp - zone.release) / span)
            global_hot = max(global_hot, hotness)
        return global_hot

    def _cadence_for(self, sev: int, trend: float, global_hot: float) -> Dict[str, int]:
        """Dynamically compute cadence based on severity, trend, and hotness"""
        # Start with base cadence
        cadence = BASE_CADENCE[sev].copy()

        # Apply distance-to-trigger scaling
        cadence['off'] = max(1, cadence['off'] - int(7 * global_hot))
        cadence['on'] = max(1, cadence['on'] + int(5 * global_hot))

        # Apply trend scaling
        if trend > 0:  # Heating trend
            cadence['off'] = max(1, int(cadence['off'] * (1 - TREND_SENSITIVITY * trend)))
            cadence['on'] = min(30, int(cadence['on'] * (1 + TREND_SENSITIVITY * trend)))
        elif trend < 0:  # Cooling trend
            cadence['off'] = min(60, int(cadence['off'] * (1 - TREND_SENSITIVITY * trend)))
            cadence['on'] = max(1, int(cadence['on'] * (1 + TREND_SENSITIVITY * trend)))

        return cadence

    def handle_state_transition(self, now: float, elapsed: float,
                                pulse_cfg: Dict[str, int], fans: Dict[str, int]):
        """Manage state transitions based on current conditions"""
        if self.state == "INITIAL_BOOST":
            if elapsed >= INITIAL_BOOST:
                self.set_profile("balanced")
                self.state = "NORMAL"

        elif self.state == "NORMAL":
            if self.current_profile == "performance":
                if elapsed >= pulse_cfg["on"]:
                    self.set_profile("balanced")
            else:  # balanced
                if elapsed >= pulse_cfg["off"] or self.analyzer.fans_too_low(fans):
                    self.set_profile("performance")

    def log_status(self, now: float, temps: Dict[str, float],
                   fans: Dict[str, int], global_sev: int, pulse_cfg: Dict[str, int]):
        """Log system status periodically"""
        if now - self.last_log < LOG_INTERVAL:
            return

        self.last_log = now
        status_lines = []

        for zone in ZONES:
            temp = temps.get(zone.name, 0.0)
            rpm = fans.get(zone.name, 0)
            trend = self.analyzer.calculate_trend(zone.name)
            status_lines.append(
                f"{zone.name}:{temp:.1f}°C/{rpm}rpm(t{trend:.2f})"
            )

        cadence_str = f"on={pulse_cfg['on']}/off={pulse_cfg['off']}"
        logging.info(
            "prof=%s sev=%d cad=%s %s",
            self.current_profile[:4],
            global_sev,
            cadence_str,
            " ".join(status_lines)
        )

    def set_profile(self, profile: str, force: bool = False):
        """Switch to new ACPI profile if needed"""
        if force or self.current_profile != profile:
            self.current_profile = profile
            self.last_switch = time.time()
            set_acpi_profile(profile)


###############################################################################
# System Functions
###############################################################################

def set_acpi_profile(profile: str) -> None:
    """Write profile to ACPI platform profile sysfs node"""
    try:
        with open(ACPI_PROFILE_PATH, "w") as f:
            f.write(profile)
        logging.debug("ACPI profile → %s", profile)
    except OSError as exc:
        logging.error("Unable to set ACPI profile: %s", exc)


def setup_logging():
    """Configure logging system"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S',
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler()
        ]
    )
    logging.info("Starting Enhanced Adaptive Fan Control")


###############################################################################
# Main Entry Point
###############################################################################

def main() -> None:
    """Main application entry point"""
    if os.geteuid() != 0:
        raise SystemExit("[ERR] Must be run as root.")

    setup_logging()
    logging.info("Boot: forcing performance for %d s", INITIAL_BOOST)

    analyzer = ThermalAnalyzer()
    controller = PulseController(analyzer)
    set_acpi_profile("performance")

    try:
        while True:
            temps, fans = SensorReader.read()
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