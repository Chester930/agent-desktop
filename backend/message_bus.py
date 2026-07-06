import asyncio
import logging
from typing import Callable, Dict, List, Any

logger = logging.getLogger("MessageBus")

class MessageBus:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(MessageBus, cls).__new__(cls, *args, **kwargs)
            cls._instance._init_bus()
        return cls._instance

    def _init_bus(self):
        self.subscribers: Dict[str, List[Callable]] = {}

    def subscribe(self, topic: str, callback: Callable[[Dict[str, Any]], Any]):
        """Subscribe to a specific topic with a callback."""
        if topic not in self.subscribers:
            self.subscribers[topic] = []
        if callback not in self.subscribers[topic]:
            self.subscribers[topic].append(callback)
            logger.info(f"Subscribed callback to topic: {topic}")

    def unsubscribe(self, topic: str, callback: Callable):
        """Unsubscribe a callback from a topic."""
        if topic in self.subscribers and callback in self.subscribers[topic]:
            self.subscribers[topic].remove(callback)
            logger.info(f"Unsubscribed callback from topic: {topic}")

    def publish(self, topic: str, message: Dict[str, Any]):
        """Publish a message to all subscribers of a topic asynchronously."""
        if topic not in self.subscribers:
            return
        
        logger.info(f"Publishing message to topic '{topic}': {message}")
        for callback in self.subscribers[topic]:
            if asyncio.iscoroutinefunction(callback):
                asyncio.create_task(callback(message))
            else:
                try:
                    callback(message)
                except Exception as e:
                    logger.error(f"Error executing callback for topic {topic}: {e}")

# Global singleton message bus instance
global_bus = MessageBus()
