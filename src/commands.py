#!/usr/bin/env python3
"""
Command pattern implementation for fan control actions.

Defines commands for user and system interactions.
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

from .config import ConfigManager
from .events import event_bus


class Command(ABC):
    """Base command interface for the Command pattern."""

    @abstractmethod
    def execute(self) -> None:
        """Execute the command."""
        pass


class SetProfileCommand(Command):
    """Command to set the ACPI profile."""

    def __init__(self, profile: str, force: bool = False, config: ConfigManager = None):
        """
        Initialize the command.
        
        Args:
            profile: ACPI profile to set ('performance' or 'balanced')
            force: If True, set the profile even if it's already set
            config: ConfigManager instance
        """
        self.profile = profile
        self.force = force
        self.config = config or ConfigManager()

    def execute(self) -> None:
        """Set the ACPI profile by writing to sysfs."""
        try:
            with open(self.config.acpi_profile_path, "w") as f:
                f.write(self.profile)
            logging.debug("ACPI profile → %s", self.profile)

            # Publish event for subscribers
            event_bus.publish("profile_changed", {
                "profile": self.profile,
                "forced": self.force
            })
        except OSError as exc:
            logging.error("Unable to set ACPI profile: %s", exc)


class LockProfileCommand(Command):
    """Command to lock the ACPI profile."""

    def __init__(self, profile: str, duration: Optional[int] = None):
        """
        Initialize command to lock profile.
        
        Args:
            profile: Profile to lock to ("performance" or "balanced")
            duration: Lock duration in seconds, or None for indefinite
        """
        self.profile = profile
        self.duration = duration

    def execute(self) -> None:
        """Lock the profile."""
        event_bus.publish("profile_locked", {
            "profile": self.profile,
            "duration": self.duration
        })

        # Also execute set profile command
        SetProfileCommand(self.profile, force=True).execute()


class UnlockProfileCommand(Command):
    """Command to unlock the ACPI profile."""

    def execute(self) -> None:
        """Unlock the profile."""
        event_bus.publish("profile_unlocked", None)
