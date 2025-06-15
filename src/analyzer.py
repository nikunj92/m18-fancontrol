#!/usr/bin/env python3
"""
Thermal analysis module.

Analyzes thermal data and calculates trends with time correction.
"""

import time
from collections import deque
from typing import Dict, Tuple, Deque

from .config import ConfigManager


class ThermalAnalyzer:
    """
    Analyzes thermal data and calculates trends with time correction.

    Maintains a moving window of temperature history for each zone, and
    computes the trend (slope) using linear regression on timestamps.
    """

    def __init__(self, config: ConfigManager = None):
        """Initialize the analyzer with temperature history for each zone."""
        self.config = config or ConfigManager()
        # Store (timestamp, temperature) pairs for accurate regression
        self.temp_history: Dict[str, Deque[Tuple[float, float]]] = {
            zone.name: deque(maxlen=self.config.history_window)
            for zone in self.config.zones
        }

    def update_history(self, temps: Dict[str, float]) -> None:
        """Update temperature history with current timestamp for each zone."""
        now = time.time()
        for zone in self.config.zones:
            if temp := temps.get(zone.name):
                self.temp_history[zone.name].append((now, temp))

    def calculate_trend(self, zone_name: str) -> float:
        """
        Calculate temperature trend for a zone (-1 to 1).
        Uses actual timestamps for accurate regression.

        Returns:
            Normalized slope in range [-1, 1].
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
        zone = next((z for z in self.config.zones if z.name == zone_name), None)
        if not zone:
            return 0.0

        # Normalize slope to range -1 to 1 based on zone thresholds
        threshold_range = zone.trigger - zone.release
        if threshold_range <= 0:
            return 0.0

        return max(-1.0, min(1.0, slope / threshold_range))

    def zone_severity(self, zone_name: str, temp: float) -> int:
        """Determine thermal severity level (0-2) for a zone."""
        zone = next((z for z in self.config.zones if z.name == zone_name), None)
        if not zone:
            return 0

        if temp >= zone.trigger:
            return 2
        if temp >= zone.release:
            return 1
        return 0

    def fans_too_low(self, fans: Dict[str, int]) -> bool:
        """Check if any fan is below minimum RPM (global check)."""
        if not fans:
            return False

        for zone in self.config.zones:
            if zone.name in fans and fans[zone.name] < zone.min_rpm:
                return True

        return False

    def calculate_global_severity(self, temps: Dict[str, float]) -> int:
        """Determine overall system thermal severity (max of all zones)."""
        max_severity = 0
        for zone in self.config.zones:
            temp = temps.get(zone.name, 0)
            severity = self.zone_severity(zone.name, temp)
            if severity > max_severity:
                max_severity = severity
        return max_severity

    def max_weighted_trend(self, temps: Dict[str, float]) -> float:
        """Calculate maximum weighted trend across all zones."""
        trends = []
        for zone in self.config.zones:
            if zone.name in temps:
                trend_val = self.calculate_trend(zone.name)
                weight = self.config.zone_weights.get(zone.name, 0.5)
                trends.append(weight * trend_val)
        return max(trends) if trends else 0.0

    def compute_global_hot(self, temps: Dict[str, float]) -> float:
        """Compute global hotness factor based on distance to triggers."""
        global_hot = 0.0
        for zone in self.config.zones:
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
