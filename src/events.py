#!/usr/bin/env python3
"""
Simple event system for communication between components.

Allows components to publish and subscribe to events.
"""

from typing import Dict, List, Callable, Any


class EventBus:
    """
    Simple event bus that allows components to publish and subscribe to events.
    
    Events are identified by a string name and can carry an arbitrary payload.
    """
    _instance = None

    def __new__(cls):
        """Singleton pattern to ensure only one event bus exists."""
        if cls._instance is None:
            cls._instance = super(EventBus, cls).__new__(cls)
            cls._instance._subscribers = {}
        return cls._instance

    def __init__(self):
        """Initialize the event bus."""
        if not hasattr(self, '_subscribers'):
            self._subscribers: Dict[str, List[Callable[[Any], None]]] = {}

    def subscribe(self, event_name: str, callback: Callable[[Any], None]) -> None:
        """
        Subscribe to an event.
        
        Args:
            event_name: Name of the event to subscribe to
            callback: Function to call when the event is published
        """
        if event_name not in self._subscribers:
            self._subscribers[event_name] = []
        self._subscribers[event_name].append(callback)

    def unsubscribe(self, event_name: str, callback: Callable[[Any], None]) -> None:
        """
        Unsubscribe from an event.
        
        Args:
            event_name: Name of the event to unsubscribe from
            callback: Function to remove from subscribers
        """
        if event_name in self._subscribers and callback in self._subscribers[event_name]:
            self._subscribers[event_name].remove(callback)

    def publish(self, event_name: str, payload: Any = None) -> None:
        """
        Publish an event with optional payload.
        
        Args:
            event_name: Name of the event to publish
            payload: Data to send with the event
        """
        if event_name in self._subscribers:
            for callback in self._subscribers[event_name]:
                callback(payload)


# Global event bus instance
event_bus = EventBus()
