#!/usr/bin/env python3
"""
Configuration manager for fan control system.

Handles loading and accessing configuration from YAML file,
with support for reloading configuration at runtime.
"""

import os
from dataclasses import dataclass
from typing import Dict, List, Any, Tuple

import yaml


@dataclass(frozen=True)
class ZoneConfig:
    """Configuration for a thermal zone.

    Each zone is defined by:
    - name: logical name
    - temp_regex: regexes to match temperature sensors
    - fan_regex: regexes to match fan sensors
    - trigger: temp above which zone is considered hot
    - release: temp below which zone is considered cool
    - min_rpm: minimum expected fan RPM (for sanity check)
    - max_rpm: observed maximum RPM (for logging/analysis)
    """
    name: str
    temp_regex: List[str]
    fan_regex: List[str]
    trigger: int
    release: int
    min_rpm: int
    max_rpm: int

    @property
    def high_rpm(self) -> int:
        """85% of max RPM for logging/analysis"""
        return int(self.max_rpm * 0.85)


class ConfigManager:
    """
    Manages loading and accessing configuration from YAML file.
    Supports reloading configuration at runtime.
    """

    _instance = None

    def __new__(cls, config_path=None):
        """Singleton pattern to ensure only one config instance exists."""
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, config_path=None):
        """Initialize the configuration manager with a config file path."""
        if self._initialized:
            return

        self.config_path = config_path
        self._config = {}
        self._zones = tuple()

        if config_path:
            self.reload()

        self._initialized = True

    def reload(self) -> None:
        """Reload configuration from YAML file."""
        if not self.config_path or not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")

        try:
            with open(self.config_path, "r") as f:
                self._config = yaml.safe_load(f)

            # Create ZoneConfig objects from YAML
            zones = []
            for zone_data in self._config.get("zone_configs", []):
                zones.append(ZoneConfig(
                    name=zone_data["name"],
                    temp_regex=zone_data["temp_regex"],
                    fan_regex=zone_data["fan_regex"],
                    trigger=zone_data["trigger"],
                    release=zone_data["release"],
                    min_rpm=zone_data["min_rpm"],
                    max_rpm=zone_data["max_rpm"]
                ))
            self._zones = tuple(zones)
        except Exception as e:
            raise ValueError(f"Error loading configuration: {str(e)}")

    @property
    def acpi_profile_path(self) -> str:
        """Path to ACPI profile sysfs node."""
        return self._config.get("acpi_profile_path", "/sys/firmware/acpi/platform_profile")

    @property
    def log_file(self) -> str:
        """Log file for daemon output."""
        return self._config.get("log_file", "/var/log/alienware_fancontrol.log")

    @property
    def poll_interval(self) -> int:
        """How often to poll sensors (seconds)."""
        return self._config.get("poll_interval", 1)

    @property
    def log_interval(self) -> int:
        """How often to write a log line (seconds)."""
        return self._config.get("log_interval", 10)

    @property
    def initial_boost(self) -> int:
        """On boot, keep performance mode for this many seconds."""
        return self._config.get("initial_boost", 5)

    @property
    def critical_temp(self) -> int:
        """Emergency: if any zone exceeds this temp (°C), lock performance."""
        return self._config.get("critical_temp", 95)

    @property
    def history_window(self) -> int:
        """Number of seconds to keep temperature history for trend calculation."""
        return self._config.get("history_window", 30)

    @property
    def trend_sensitivity(self) -> float:
        """How aggressively to adjust cadence based on trend (0-1)."""
        return self._config.get("trend_sensitivity", 0.3)

    @property
    def emergency_debounce(self) -> int:
        """How many consecutive seconds above CRITICAL_TEMP before emergency triggers."""
        return self._config.get("emergency_debounce", 3)

    @property
    def base_cadence(self) -> Dict[int, Dict[str, int]]:
        """Base cadence for each severity level (will be dynamically adjusted)."""
        return self._config.get("base_cadence", {
            0: {"on": 0, "off": 9999},
            1: {"on": 1, "off": 30},
            2: {"on": 4, "off": 12},
        })

    @property
    def zone_weights(self) -> Dict[str, float]:
        """Importance weights for each zone when calculating global trend."""
        return self._config.get("zone_weights", {
            "cpu": 1.0,
            "gpu": 1.0,
            "memory": 0.6,
            "ambient": 0.4
        })

    @property
    def zones(self) -> Tuple[ZoneConfig, ...]:
        """List of all thermal zones to monitor."""
        return self._zones

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value by key."""
        return self._config.get(key, default)
