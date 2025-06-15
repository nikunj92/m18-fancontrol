#!/usr/bin/env python3
"""Autofan Control Daemon with Enhanced Adaptive Cadence

A modular approach to fan control on Alienware laptops using ACPI profiles.
Provides indirect fan control by pulsing between performance and balanced profiles.

Features:
- Configuration loaded from YAML.
- Time-corrected linear regression for thermal trends.
- Weighted zone trends.
- Adaptive cadence recomputation each tick.
- Distance-to-trigger hotness factor.
- Global fan RPM monitoring.
- Debounced emergency handling.
- Profile locking capability via command-line arguments.

See README.md for design notes and configuration.
"""

import logging
import os
import sys
import time
import argparse
from pathlib import Path

# Ensure the package components can be imported
# This might not be strictly necessary if running with `python -m src.autofan`
# but helps if running the script directly from the src directory for development.
# For production, consider proper packaging and installation.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import ConfigManager
from src.sensors import SensorReader
from src.analyzer import ThermalAnalyzer
from src.controller import PulseController
from src.commands import SetProfileCommand, LockProfileCommand
# Event bus is implicitly used by components, no direct import needed here unless for specific main script logic.

def setup_logging(log_file_path: str, log_level_str: str = "INFO"):
    """Configure logging system for both file and console output."""
    # TODO - Use a rotating file handler to avoid log file bloat (e.g., logging.handlers.RotatingFileHandler)
    # TODO - Consider logging only when there are changes to state or significant events, or make debug logging more extensive.
    
    log_level = getattr(logging, log_level_str.upper(), logging.INFO)

    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(module)s: %(message)s',
        datefmt='%H:%M:%S',
        handlers=[
            logging.FileHandler(log_file_path),
            logging.StreamHandler(sys.stdout) # Ensure logs go to stdout for systemd or similar
        ]
    )
    logging.info("Starting Enhanced Adaptive Fan Control")
    logging.debug(f"Logging initialized at level {log_level_str.upper()}")


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Alienware m18 Adaptive Fan Control Daemon.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to YAML configuration file. If not provided, searches in standard locations."
    )
    parser.add_argument(
        "--lock-profile",
        choices=["performance", "balanced"],
        help="Lock to a specific ACPI profile and exit automatic control."
    )
    parser.add_argument(
        "--lock-duration",
        type=int,
        help="Duration (in seconds) to lock the profile. If not provided, lock is indefinite."
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set the logging level."
    )
    return parser.parse_args()


def find_config_file(specified_path: str = None) -> str:
    """
    Find the configuration file.
    Searches in order: specified path, script's directory, project root, /etc, user's config.
    """
    if specified_path and os.path.exists(specified_path):
        return specified_path

    # Path relative to this script file (src/autofan.py)
    script_dir = Path(__file__).resolve().parent
    project_root_dir = script_dir.parent # Assumes src is in project root

    search_paths = [
        script_dir / "config.yaml",
        project_root_dir / "config.yaml", # For finding config.yaml at project root
        Path("/etc/fancontrol/config.yaml"),
        Path.home() / ".config/fancontrol/config.yaml"
    ]

    for path in search_paths:
        if path.exists():
            return str(path)
            
    return None


def main() -> None:
    """Main application entry point.

    Requires root privileges. Initializes components and enters the main control loop.
    Handles KeyboardInterrupt and other exceptions gracefully.
    [TODO] Space to improve without root perhaps (see README). This might involve
           setting up udev rules or polkit permissions for the ACPI profile path.
    """
    args = parse_args()

    if os.geteuid() != 0:
        # This check is important as writing to ACPI profile path requires root.
        sys.exit("[ERR] This script must be run as root to control ACPI platform profiles.")

    config_file_path = find_config_file(args.config)
    if not config_file_path:
        sys.exit(
            "[ERR] Configuration file (config.yaml) not found. "
            "Please provide a path using --config or place it in a standard location (e.g., next to the script, project root, /etc/fancontrol/, ~/.config/fancontrol/)."
        )

    # Initialize ConfigManager first, as other components depend on it.
    # The ConfigManager is a singleton; this initializes it if not already.
    config = ConfigManager(config_file_path)
    
    # Setup logging after config is loaded (to get log_file path and level if defined there)
    # Command-line log level overrides config for now.
    log_file_from_config = config.log_file 
    setup_logging(log_file_from_config, args.log_level)
    logging.info(f"Using configuration from: {config_file_path}")
    logging.debug(f"Effective poll interval: {config.poll_interval}s, log interval: {config.log_interval}s")

    # Initialize other components
    # These will use the singleton ConfigManager instance implicitly or explicitly if passed
    analyzer = ThermalAnalyzer(config=config)
    controller = PulseController(analyzer=analyzer, config=config)
    sensor_reader = SensorReader(config=config)

    # Initial boot sequence: force performance mode for a defined duration
    logging.info(f"Initial boot: forcing 'performance' profile for {config.initial_boost} seconds.")
    initial_set_profile_cmd = SetProfileCommand(profile="performance", force=True, config=config)
    initial_set_profile_cmd.execute()
    # Note: The controller's INITIAL_BOOST state will also handle this duration.

    # Handle command-line profile lock, if specified
    if args.lock_profile:
        logging.info(
            f"Command-line lock: locking profile to '{args.lock_profile}' "
            f"{('for ' + str(args.lock_duration) + 's' if args.lock_duration else 'indefinitely')}."
        )
        lock_cmd = LockProfileCommand(profile=args.lock_profile, duration=args.lock_duration)
        # LockProfileCommand internally uses event_bus to notify the controller
        # and also executes SetProfileCommand.
        lock_cmd.execute()
        # If locked via CLI, the main loop might not be strictly necessary if we want it to just set and exit.
        # However, the current design has the controller manage the lock state, so the loop should run.

    try:
        logging.info("Starting main control loop. Press Ctrl+C to exit.")
        while True:
            temps, fans = sensor_reader.read()
            if not temps and not fans:
                logging.warning("No sensor data read. Check sensor configuration and hardware.")
            
            controller.tick(temps, fans)
            time.sleep(config.poll_interval)
    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt received. Setting profile to 'balanced' and exiting.")
        # Ensure a safe default state on exit
        final_set_profile_cmd = SetProfileCommand(profile="balanced", force=True, config=config)
        final_set_profile_cmd.execute()
        logging.info("Exited cleanly.")
    except Exception as e:
        logging.critical("An unhandled exception occurred in the main loop!", exc_info=True)
        logging.error(f"Fatal error: {e}. Forcing 'performance' profile as a precaution and exiting.")
        try:
            # Attempt to set performance mode as a fallback on critical error
            critical_fallback_cmd = SetProfileCommand(profile="performance", force=True, config=config)
            critical_fallback_cmd.execute()
        except Exception as final_exc:
            logging.error(f"Could not set fallback profile: {final_exc}")
        sys.exit(1) # Indicate an error exit


if __name__ == "__main__":
    # This allows the script to be run directly.
    # For systemd or similar, ensure the python interpreter and path to this script are correct.
    # e.g., /usr/bin/python3 /path/to/m18-fancontrol/src/autofan.py
    main()
