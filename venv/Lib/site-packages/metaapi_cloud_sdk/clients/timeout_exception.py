import asyncio


class TimeoutException(Exception):
    """Exception which indicates that MetaTrader terminal did not start yet. You need to wait until account is
    connected and retry.
    """

    def __init__(self, message: str):
        """Initializes the timeout exception

        Args:
            message: Exception message.
        """
        is_loop_running = False
        try:
            loop = asyncio.get_running_loop()
            is_loop_running = loop.is_running()
        except Exception:
            pass
        if not is_loop_running:
            message += ' Warning: no running event loop. The MetaApi SDK relies on asyncio library to work properly. '\
                       'Please note that we detected that the asyncio event loop is currently not running which means '\
                       'that your application is not properly organized. Please refer to the asyncio library '\
                       'documentation to fix this issue. Please note that some python frameworks may impose '\
                       'limitations on how asyncio can be used across application'
        super().__init__(message)
