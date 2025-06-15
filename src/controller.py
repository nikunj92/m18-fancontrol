#!/usr/bin/env python3
"""
Fan control logic module.

Manages ACPI profile switching with enhanced adaptive cadence.
"""

import logging
import time
from typing import Dict, Any

from .analyzer import ThermalAnalyzer
from .commands import SetProfileCommand
from .config import ConfigManager
from .events import event_bus


class PulseController:
    """
    Manages ACPI profile switching with enhanced adaptive cadence.

    Implements a state machine:
    - INITIAL_BOOST: On boot, force performance mode for a few seconds.
    - NORMAL: Pulse between performance and balanced based on thermal state.
    - PROFILE_LOCKED: User has locked the profile, override automatic control.
    - Handles emergency mode if critical temperature is reached.
    """

    def __init__(self, analyzer: ThermalAnalyzer = None, config: ConfigManager = None):
        """Initialize the controller with dependencies."""
        self.config = config or ConfigManager()
        self.analyzer = analyzer or ThermalAnalyzer(self.config)
        self.state = "INITIAL_BOOST"
        self.current_profile = "performance"
        self.last_switch = time.time()
        self.emergency = False
        self.last_log = 0
        self.last_global_severity = 0
        self.over_counter = 0  # For emergency debounce

        # Profile locking (new feature)
        self.profile_locked = False
        self.locked_profile = None
        self.lock_until = None

        # Subscribe to commands
        event_bus.subscribe("profile_locked", self._handle_profile_lock)
        event_bus.subscribe("profile_unlocked", self._handle_profile_unlock)

    def tick(self, temps: Dict[str, float], fans: Dict[str, int]) -> None:
        """
        Advance the controller state by one tick.

        Reads current temps/fans, updates history, checks for emergency,
        computes cadence, and switches ACPI profile as needed.

        TODO: Let the system commit and ride out the cadence, then reassess.
        Currently, cadence is recalculated every tick, so mid-pulse overrides
        always happen, breaking the spirit of duration-based control.
        """
        now = time.time()
        elapsed = now - self.last_switch

        # Check if profile lock has expired
        if self.profile_locked and self.lock_until and now >= self.lock_until:
            self._handle_profile_unlock(None)

        # Update temperature history
        self.analyzer.update_history(temps)

        # Emergency temperature handling with debounce
        self.handle_emergency(temps)

        if self.emergency or self.profile_locked:
            # Skip normal control logic if in emergency or locked state
            self.log_status(now, temps, fans)
            return

        # Calculate global severity
        global_sev = self.analyzer.calculate_global_severity(temps)
        self.last_global_severity = global_sev

        # Compute cadence dynamically
        trend = self.analyzer.max_weighted_trend(temps)
        global_hot = self.analyzer.compute_global_hot(temps)
        pulse_cfg = self._cadence_for(global_sev, trend, global_hot)

        # State machine logic
        self.handle_state_transition(now, elapsed, pulse_cfg, fans)

        # Periodic logging
        self.log_status(now, temps, fans, global_sev, pulse_cfg)

    def handle_emergency(self, temps: Dict[str, float]) -> None:
        """
        Handle critical temperature situation with debounce.

        If any zone exceeds CRITICAL_TEMP for EMERGENCY_DEBOUNCE consecutive
        ticks, lock performance mode until temps normalize.

        TODO: Emergency mode can be triggered by micro-spikes on a single
        sensor (e.g., a single CPU core). Consider using a moving average or
        smarter aggregation instead of max() to avoid false positives.
        """
        critical = False
        for temp in temps.values():
            if temp >= self.config.critical_temp:
                critical = True
                break

        if critical:
            self.over_counter += 1
        else:
            self.over_counter = 0

        if self.over_counter >= self.config.emergency_debounce:
            if not self.emergency:
                logging.critical("Temperature ≥ %d °C for %ds — locking performance",
                                 self.config.critical_temp, self.config.emergency_debounce)
                self._set_profile("performance", force=True)
                self.emergency = True
        elif self.emergency and self.over_counter == 0:
            # Return from emergency if temps normalized
            self.emergency = False
            logging.warning("Temperature normalized, exiting emergency mode")

    def _cadence_for(self, sev: int, trend: float, global_hot: float) -> Dict[str, int]:
        """Dynamically compute cadence based on severity, trend, and hotness."""
        # Start with base cadence
        base_cadence = self.config.base_cadence.get(sev, {"on": 1, "off": 10})
        cadence = base_cadence.copy()

        # Apply distance-to-trigger scaling
        cadence['off'] = max(1, cadence['off'] - int(7 * global_hot))
        cadence['on'] = max(1, cadence['on'] + int(5 * global_hot))

        # Apply trend scaling
        if trend > 0:  # Heating trend
            cadence['off'] = max(1, int(cadence['off'] * (1 - self.config.trend_sensitivity * trend)))
            cadence['on'] = min(30, int(cadence['on'] * (1 + self.config.trend_sensitivity * trend)))
        elif trend < 0:  # Cooling trend
            cadence['off'] = min(60, int(cadence['off'] * (1 - self.config.trend_sensitivity * trend)))
            cadence['on'] = max(1, int(cadence['on'] * (1 + self.config.trend_sensitivity * trend)))

        return cadence

    def handle_state_transition(self, now: float, elapsed: float,
                                pulse_cfg: Dict[str, int], fans: Dict[str, int]) -> None:
        """
        Manage state transitions based on current conditions.

        TODO: Currently, state transitions can override the intended pulse durations.
        """
        if self.state == "INITIAL_BOOST":
            if elapsed >= self.config.initial_boost:
                self._set_profile("balanced")
                self.state = "NORMAL"

        elif self.state == "NORMAL":
            if self.current_profile == "performance":
                if elapsed >= pulse_cfg["on"]:
                    self._set_profile("balanced")
            else:  # balanced
                if elapsed >= pulse_cfg["off"] or self.analyzer.fans_too_low(fans):
                    self._set_profile("performance")

    def log_status(self, now: float, temps: Dict[str, float],
                   fans: Dict[str, int], global_sev: int = None, pulse_cfg: Dict[str, int] = None) -> None:
        """Log system status periodically."""
        if now - self.last_log < self.config.log_interval:
            return

        self.last_log = now
        status_lines = []

        for zone in self.config.zones:
            temp = temps.get(zone.name, 0.0)
            rpm = fans.get(zone.name, 0)
            trend = self.analyzer.calculate_trend(zone.name)
            status_lines.append(
                f"{zone.name}:{temp:.1f}°C/{rpm}rpm(t{trend:.2f})"
            )

        # Get state info
        if self.emergency:
            state_str = "emergency"
        elif self.profile_locked:
            state_str = f"locked:{self.locked_profile}"
        elif self.state == "INITIAL_BOOST":
            state_str = "boost"
        else:
            state_str = "normal"

        # Get cadence info if available
        if pulse_cfg:
            cadence_str = f"on={pulse_cfg['on']}/off={pulse_cfg['off']}"
        else:
            cadence_str = "n/a"

        # Get severity info
        sev_str = str(global_sev) if global_sev is not None else "?"

        logging.info(
            "prof=%s state=%s sev=%s cad=%s %s",
            self.current_profile[:4],
            state_str,
            sev_str,
            cadence_str,
            " ".join(status_lines)
        )

    def _set_profile(self, profile: str, force: bool = False) -> None:
        """Set ACPI profile using command pattern."""
        if force or self.current_profile != profile:
            cmd = SetProfileCommand(profile, force, self.config)
            cmd.execute()
            self.current_profile = profile
            self.last_switch = time.time()

    def _handle_profile_lock(self, data: Dict[str, Any]) -> None:
        """Handle profile lock command."""
        if not data:
            return

        profile = data.get("profile")
        duration = data.get("duration")

        if profile not in ("performance", "balanced"):
            logging.error("Invalid profile for locking: %s", profile)
            return

        self.profile_locked = True
        self.locked_profile = profile

        if duration:
            self.lock_until = time.time() + duration
        else:
            self.lock_until = None

        logging.info(
            "Profile locked to %s%s",
            profile,
            f" for {duration}s" if duration else " indefinitely"
        )

        # Apply the locked profile
        self._set_profile(profile, force=True)

    def _handle_profile_unlock(self, _) -> None:
        """Handle profile unlock command."""
        if self.profile_locked:
            self.profile_locked = False
            self.locked_profile = None
            self.lock_until = None
            logging.info("Profile unlocked, resuming automatic control")
