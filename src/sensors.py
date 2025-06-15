#!/usr/bin/env python3
"""
Sensor reading module.

Reads temperature and fan data from hardware sensors.
"""

import glob
import os
import re
from collections import defaultdict
from typing import Dict, List, Tuple

from .config import ConfigManager


class SensorReader:
    """
    Reads temperature and fan data from hardware sensors.

    Scans /sys/class/hwmon for all available sensors, matches them to zones
    using regexes, and aggregates the maximum value per zone.
    """

    def __init__(self, config: ConfigManager = None):
        """Initialize the sensor reader with configuration."""
        self.config = config or ConfigManager()

    @staticmethod
    def _matches_any(regexes: List[str], text: str) -> bool:
        """Case-insensitive search against regex patterns."""
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in regexes)

    def read(self) -> Tuple[Dict[str, float], Dict[str, int]]:
        """
        Gather temperatures (°C) and fan RPMs from /sys/class/hwmon.

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
                self._process_sensor(entry, sensor_path, device, temps, fans)

        # Collapse lists to single value per zone
        agg_temps = {z: max(v) for z, v in temps.items() if v}
        agg_fans = {z: max(v) for z, v in fans.items() if v}
        return agg_temps, agg_fans

    def _process_sensor(self, entry: str, sensor_path: str, device: str,
                        temps: Dict[str, List[float]],
                        fans: Dict[str, List[int]]):
        """Process individual sensor entries and assign to temp or fan."""
        # Temperature sensors
        if entry.startswith("temp") and entry.endswith("_input"):
            self._process_temp_sensor(entry, sensor_path, device, temps)
        # Fan sensors
        elif entry.startswith("fan") and entry.endswith("_input"):
            self._process_fan_sensor(entry, sensor_path, device, fans)

    def _process_temp_sensor(self, entry: str, sensor_path: str, device: str,
                             temps: Dict[str, List[float]]):
        """Process temperature sensor input and assign to zone."""
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
        self._assign_to_zone(key, value, temps, is_temp=True)

    def _process_fan_sensor(self, entry: str, sensor_path: str, device: str,
                            fans: Dict[str, List[int]]):
        """Process fan sensor input and assign to zone."""
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
        self._assign_to_zone(key, rpm, fans, is_temp=False)

    def _assign_to_zone(self, key: str, value: float,
                        data: Dict[str, List[float]], is_temp: bool):
        """Assign sensor reading to appropriate zone based on regex match."""
        for zone in self.config.zones:
            regex_list = zone.temp_regex if is_temp else zone.fan_regex
            if self._matches_any(regex_list, key):
                data[zone.name].append(value)
                break
