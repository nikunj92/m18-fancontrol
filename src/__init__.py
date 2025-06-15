"""
Alienware Fan Control Package.

A modular system for controlling fan behavior on Alienware laptops
by manipulating the ACPI platform profile.
"""

__version__ = "0.2.0"

# Core components
from .config import ConfigManager, ZoneConfig
from .events import event_bus
from .sensors import SensorReader
from .analyzer import ThermalAnalyzer
from .controller import PulseController

# Commands
from .commands import SetProfileCommand, LockProfileCommand, UnlockProfileCommand

# Re-export key components for easier importing by external modules/scripts if any.
# For internal use, direct imports like `from .config import ConfigManager` are preferred.
__all__ = [
    "ConfigManager", "ZoneConfig",
    "event_bus",
    "SensorReader",
    "ThermalAnalyzer",
    "PulseController",
    "SetProfileCommand", "LockProfileCommand", "UnlockProfileCommand",
    "__version__"
]
