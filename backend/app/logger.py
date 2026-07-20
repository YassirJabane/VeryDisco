import logging
import asyncio
import sys
from typing import Set, Any, Optional
from datetime import datetime

# Global state for SSE clients and database tracking
log_subscribers: Set[asyncio.Queue] = set()
db_ref: Any = None
current_run_id: Optional[int] = None

class QueueHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))

    def emit(self, record):
        log_msg = self.format(record)
        log_level = record.levelname
        timestamp = datetime.utcnow().isoformat()
        
        payload = {
            "timestamp": timestamp,
            "level": log_level,
            "message": log_msg,
            "run_id": current_run_id
        }

        # Broadcast to any active SSE subscribers
        for q in list(log_subscribers):
            try:
                q.put_nowait(payload)
            except Exception:
                pass

        # Also write to SQLite DB safely
        if db_ref:
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon_threadsafe(
                    loop.create_task,
                    db_ref.add_log(log_level, record.getMessage(), current_run_id)
                )
            except RuntimeError:
                # No running event loop — skip DB logging for this entry
                pass
            except Exception:
                pass

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
