import logging
import os
import sys

import loguru
from pipecat.utils.run_context import run_id_var

from api.constants import (
    ENVIRONMENT,
    LOG_COMPRESSION,
    LOG_FILE_PATH,
    LOG_LEVEL,
    LOG_RETENTION,
    LOG_ROTATION_SIZE,
    SERIALIZE_LOG_OUTPUT,
)
from api.enums import Environment
from api.utils.worker import get_worker_id, is_worker_process

# Track if logging has been initialized
_logging_initialized = False


class InterceptHandler(logging.Handler):
    """
    Intercept standard library logging calls and redirect them to loguru.
    This allows us to capture uvicorn and other library logs through loguru.
    """

    def emit(self, record):
        # Get corresponding Loguru level if it exists
        try:
            level = loguru.logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Use the original record's information instead of trying to find the caller
        # This preserves the logger name (e.g., "uvicorn.access") in the logs
        loguru.logger.patch(lambda r: r.update(name=record.name)).opt(
            exception=record.exc_info
        ).log(level, record.getMessage())


def inject_run_id(record):
    """Inject run_id from context variable into log record"""
    record["extra"]["run_id"] = run_id_var.get()


def install_asyncio_ice_teardown_guard() -> None:
    """Suppress benign aioice STUN retry noise after WebRTC teardown.

    aiortc/aioice leaves ``Transaction.__retry`` TimerHandles scheduled after
    ``RTCPeerConnection.close()``. When they fire on an already-closed
    datagram transport (``_sock=None``) Python logs
    ``Exception in callback Transaction.__retry()`` on every retry, and on
    Python 3.13 ``_SelectorTransport._fatal_error`` can itself raise
    ``AttributeError: 'NoneType' ... call_exception_handler`` because
    ``self._loop`` is already ``None``.

    This installs a loop exception handler that downgrades that specific
    noise to debug, and patches ``_fatal_error`` to no-op when the loop is
    gone. Must be called from async context (e.g. FastAPI lifespan).
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    def _is_ice_teardown_noise(context) -> bool:
        message = str(context.get("message", ""))
        exc = context.get("exception")
        handle_repr = str(context.get("handle", ""))
        exc_text = f"{type(exc).__name__}: {exc}" if exc else ""
        blob = f"{message} {handle_repr} {exc_text}"
        return (
            "Transaction.__retry" in blob
            or "_SelectorDatagramTransport closed" in blob
            or "call_exception_handler" in blob
        )

    default_handler = loop.get_exception_handler()

    def _handler(l, context):
        if _is_ice_teardown_noise(context):
            loguru.logger.debug(
                "Suppressed benign ICE teardown timer: {}", context.get("message")
            )
            return
        if default_handler is not None:
            default_handler(l, context)
        else:
            l.default_exception_handler(context)

    loop.set_exception_handler(_handler)

    # Guard stdlib against _loop=None race in _fatal_error.
    try:
        from asyncio import selector_events

        orig_fatal = selector_events._SelectorTransport._fatal_error

        def _safe_fatal(self, exc, message="Fatal error on transport"):
            if getattr(self, "_loop", None) is None:
                return
            return orig_fatal(self, exc, message)

        if getattr(selector_events._SelectorTransport._fatal_error, "_dograh_guarded", False) is not True:
            selector_events._SelectorTransport._fatal_error = _safe_fatal
            selector_events._SelectorTransport._fatal_error._dograh_guarded = True  # type: ignore[attr-defined]
    except Exception:
        # Never break startup because of a hardening patch.
        pass


def setup_logging():
    """Set up logging for the main application"""
    global _logging_initialized

    # Return early if already initialized
    if _logging_initialized:
        return

    log_level = LOG_LEVEL

    # Don't setup logging in test environment
    if ENVIRONMENT == Environment.TEST.value:
        return

    # Remove default loguru handler
    try:
        loguru.logger.remove(0)
    except ValueError:
        # Handler might already be removed
        pass

    # Set default extra values on the shared core so ALL logger references
    # (including ones imported before this runs) have run_id available.
    loguru.logger.configure(extra={"run_id": None})

    # Patch loguru to inject run_id
    patched = loguru.logger.patch(inject_run_id)

    log_format = "{time:YYYY-MM-DD HH:mm:ss.SSS} | <level>{level}</level> | [run_id={extra[run_id]}] | {file.name}:{line} | {message}"

    # Add handler - either file or console
    if LOG_FILE_PATH:
        # Determine the actual log file path
        actual_log_path = LOG_FILE_PATH

        # If we're in a worker process, append worker ID to the filename
        if is_worker_process():
            worker_id = get_worker_id()
            # Split the path to insert worker ID before extension
            base_path, ext = os.path.splitext(LOG_FILE_PATH)
            actual_log_path = f"{base_path}-worker-{worker_id}{ext}"

        patched.add(
            actual_log_path,
            level=log_level,
            serialize=SERIALIZE_LOG_OUTPUT,  # Use JSON serialization for structured logs
            enqueue=True,  # Thread-safe writing
            backtrace=True,  # Include full traceback in exceptions
            diagnose=False,  # Don't include local variables in traceback for security
            rotation=LOG_ROTATION_SIZE,  # Rotate when file reaches size limit
            retention=LOG_RETENTION,  # Keep old logs for this duration
            compression=LOG_COMPRESSION,  # Compress rotated logs
        )
    else:
        # Console handler (existing behavior)
        patched.add(
            sys.stdout,
            format=log_format,
            level=log_level,
            colorize=True,
        )

    loguru.logger = patched

    # Intercept standard library logging (uvicorn, etc.) and redirect to loguru
    # Set level to INFO to avoid debug logs from libraries
    logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO, force=True)

    # Specifically intercept uvicorn loggers with INFO level
    for logger_name in ["uvicorn", "uvicorn.error", "uvicorn.access"]:
        logging_logger = logging.getLogger(logger_name)
        logging_logger.handlers = [InterceptHandler()]
        logging_logger.setLevel(logging.INFO)
        logging_logger.propagate = False

    # MCP SDK logs a line per request lifecycle event; child loggers inherit.
    logging.getLogger("mcp").setLevel(logging.WARNING)

    _logging_initialized = True
