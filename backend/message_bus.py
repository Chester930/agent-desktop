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
        self._pending_tasks: set = set()

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

    def _on_task_done(self, topic: str, task: "asyncio.Task"):
        self._pending_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(f"Error in async subscriber for topic {topic}: {exc}", exc_info=exc)

    def publish(self, topic: str, message: Dict[str, Any]):
        """Publish a message to all subscribers of a topic asynchronously."""
        if topic not in self.subscribers:
            return

        logger.info(f"Publishing message to topic '{topic}': {message}")
        for callback in self.subscribers[topic]:
            if asyncio.iscoroutinefunction(callback):
                try:
                    task = asyncio.create_task(callback(message))
                except RuntimeError as e:
                    # No running event loop (e.g. called from sync/test context)
                    logger.error(f"Cannot schedule async callback for topic {topic}: {e}")
                    continue
                self._pending_tasks.add(task)
                task.add_done_callback(lambda t, _topic=topic: self._on_task_done(_topic, t))
            else:
                try:
                    callback(message)
                except Exception as e:
                    logger.error(f"Error executing callback for topic {topic}: {e}")

# Global singleton message bus instance
global_bus = MessageBus()
