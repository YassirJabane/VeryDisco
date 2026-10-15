import logging
import asyncio
import sys
from typing import Set, Any, Optional
from datetime import datetime, timezone
import threading

# Global state for SSE clients and database tracking
log_subscribers: Set[asyncio.Queue] = set()
db_ref: Any = None
current_run_id: Optional[int] = None
boot_started_at = datetime.now(timezone.utc).isoformat()
_loop: Optional[asyncio.AbstractEventLoop] = None
_writer: Optional[asyncio.Task] = None
_pending: list[dict] = []
_pending_lock = threading.Lock()
_queue: Optional[asyncio.Queue] = None

class QueueHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))

    def emit(self, record):
        message = record.getMessage()
        if record.exc_info:
            message += '\n' + logging.Formatter().formatException(record.exc_info)
        if record.stack_info:
            message += '\n' + record.stack_info
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname, "message": message,
            "run_id": current_run_id,
        }
        with _pending_lock:
            if _loop is None or _queue is None or _loop.is_closed():
                _pending.append(payload)
                return
            _loop.call_soon_threadsafe(_queue.put_nowait, payload)


async def _persist_and_publish():
    while True:
        payload = await _queue.get()
        try:
            if isinstance(payload, asyncio.Future):
                if not payload.done():
                    payload.set_result(None)
                continue
            payload["id"] = await db_ref.add_log(
                payload["level"], payload["message"], payload["run_id"], timestamp=payload["timestamp"],
            )
            for subscriber in tuple(log_subscribers):
                try:
                    subscriber.put_nowait(payload)
                except asyncio.QueueFull:
                    log_subscribers.discard(subscriber)
                    subscriber.get_nowait()
                    subscriber.put_nowait(None)
        except Exception as error:
            print(f"Log persistence failed: {error}: {payload['message']}", file=sys.stderr)
        finally:
            _queue.task_done()


def attach_database(database):
    global db_ref, _loop, _queue, _writer
    db_ref = database
    with _pending_lock:
        _loop = asyncio.get_running_loop()
        _queue = asyncio.Queue()
        for payload in _pending:
            _queue.put_nowait(payload)
        _pending.clear()
    _writer = asyncio.create_task(_persist_and_publish())


async def flush_logs():
    if _queue is not None:
        await asyncio.sleep(0)
        # A watermark waits for prior logs, not an indefinitely growing live tail.
        barrier = asyncio.get_running_loop().create_future()
        _queue.put_nowait(barrier)
        await barrier


async def shutdown_logging():
    global _writer, _loop, _queue
    await flush_logs()
    if _writer is not None:
        _writer.cancel()
        await asyncio.gather(_writer, return_exceptions=True)
    with _pending_lock:
        _writer = _loop = _queue = None

def setup_logging(level_name: str = "INFO"):
    logger = logging.getLogger("verydisco")
    # Reset existing handlers to prevent duplicates during reload
    logger.handlers = []
    
    level = getattr(logging, level_name.upper(), logging.INFO)
    logger.setLevel(level)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    console_handler.setLevel(level)
    logger.addHandler(console_handler)

    # SSE / DB handler
    queue_handler = QueueHandler()
    queue_handler.setLevel(level)
    logger.addHandler(queue_handler)

    logger.info(f"Logging initialized with level {level_name}")
    return logger

def get_logger():
    return logging.getLogger("verydisco")
