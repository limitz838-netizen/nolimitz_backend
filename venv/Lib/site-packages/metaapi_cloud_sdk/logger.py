import asyncio
import logging
from datetime import datetime
from logging import Logger
from typing import Callable
import psutil
from .metaapi.models import string_format_error
from pathlib import Path

logging_enabled = False


class LoggerManager:
    """Manages loggers of the entire sdk."""

    @staticmethod
    def use_logging():
        """Enables using Logging logger with extended log levels for debugging instead of
        print functions. Note that Logging configuration is performed by the user."""
        global logging_enabled
        logging_enabled = True

    @staticmethod
    def get_logger(category):
        """Creates a new logger for specified category.

        Args:
            category: Logger category.

        Returns:
            Created logger.
        """
        if logging_enabled:
            logger = logging.getLogger(category)
            original_log = logger._log

            def logging_func(level, msg: str or Callable, args, exc_info=None, extra=None, stack_info=False,
                             stacklevel=1):
                if isinstance(msg, Callable):
                    msg = msg()
                original_log(level, msg, args, exc_info, extra, stack_info, stacklevel)
            logger._log = logging_func
            return logger
        else:
            return NativeLogger(category)


class NativeLogger(Logger):
    """Native logger that uses print function."""

    def debug(self, msg, *args, **kwargs):
        # this logger does not print debug messages
        pass

    def info(self, msg, *args, **kwargs):
        self._log('INFO', msg, args)

    def warning(self, msg, *args, **kwargs):
        self._log('WARN', msg, args)

    def error(self, msg, *args, **kwargs):
        self._log('ERROR', msg, args)

    def exception(self, msg, *args, **kwargs):
        self._log('ERROR', msg, args)

    def _log(self, level: str, msg, args, exc_info=None, extra=None, stack_info: bool = None,
             stacklevel: int = None) -> None:
        if isinstance(msg, Callable):
            msg = msg()
        err_msg = ''
        if level == 'ERROR' and len(args) and isinstance(args[0], Exception):
            err_msg = string_format_error(args[0])
        print(f'[{datetime.now().isoformat()}] [{level.upper()}] {msg} ', err_msg)


class SocketLogger(NativeLogger):
    """Records SocketIO logs into a designated file."""

    def __init__(self, path: str):
        super().__init__('SocketIO')
        self._path = path
        self._record_interval: asyncio.Task or None = None
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)

    def debug(self, msg, *args, **kwargs):
        self._log('DEBUG', msg, args)

    def start(self):
        """Initializes the socket logger."""
        async def record_job():
            while True:
                await asyncio.sleep(60)
                await self._record_stats()

        psutil.cpu_percent(None)
        if not self._record_interval:
            self._record_interval = asyncio.create_task(record_job())

    def stop(self):
        """Deinitializes the socket logger."""
        self._record_interval.cancel()
        self._record_interval = None

    def _log(self, level: str, msg, args, exc_info=None, extra=None, stack_info: bool = None,
             stacklevel: int = None) -> None:
        if isinstance(msg, Callable):
            msg = msg()
        try:
            msg = self._remove_auth_token(msg)
            if args:
                formatted_args = []
                for arg in args:
                    if isinstance(arg, str):
                        arg = self._remove_auth_token(arg)
                        if len(arg) > 500:
                            arg = f'{arg[:500]}...'
                    formatted_args.append(arg)
                args = tuple(formatted_args)
                msg = msg % args
            with open(self._path, "a", encoding='utf-8') as f:
                f.write(f'[{datetime.now().isoformat()}] [{level.upper()}] {msg} \n')
        except Exception as err:
            print(f'[{datetime.now().isoformat()}] [ERROR] ' + string_format_error(err))

    async def _record_stats(self):
        try:
            cpu = psutil.cpu_percent(None)
            ram = psutil.virtual_memory().percent
            msg = f'CPU usage: {cpu:.1f}%, RAM usage: {ram:.1f}%'
            self.info(msg)
        except Exception as err:
            self.error('Error recording hardware stats ' + string_format_error(err))

    @staticmethod
    def _remove_auth_token(string):
        if '?auth-token=' in string:
            string = string.split('?auth-token', 1)[0]
        return string
