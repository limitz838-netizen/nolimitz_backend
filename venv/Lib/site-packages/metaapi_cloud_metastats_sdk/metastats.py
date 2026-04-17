import traceback
from typing import Optional

from typing_extensions import TypedDict

from .clients.domain_client import DomainClient
from .clients.http_client import HttpClient, RetryOpts
from .clients.metastats_client import MetaStatsClient


class ConnectionOptions(TypedDict):
    """Connection options."""
    requestTimeout: Optional[float]
    """Request timeout in seconds, default is 60."""
    domain: Optional[str]
    """Request domain, default 'agiliumtrade.agiliumtrade.ai.'"""
    retryOpts: Optional[RetryOpts]
    """Retry options."""


class MetaStats:
    """MetaStats API SDK."""

    def __init__(self, token: str, opts: ConnectionOptions = None):
        """Initializes MetaStats class instance.

        Args:
            token: Authorization token.
            opts: Connection options.
        """
        opts: ConnectionOptions = opts or {}
        http_client = HttpClient(opts['requestTimeout'] if 'requestTimeout' in opts else None,
                                 opts['retryOpts'] if 'retryOpts' in opts else None)
        domain_client = DomainClient(http_client, token, opts['domain'] if 'domain' in opts else None)
        self._metastats_client = MetaStatsClient(domain_client)

    @property
    def get_metrics(self):
        """Returns the get_metrics MetaStatsClient method bound to the MetaStatsClient instance.

        Returns:
            get_metrics MetaStatsClient method.
        """
        return self._metastats_client.get_metrics

    @property
    def get_account_trades(self):
        """Returns the get_account_trades MetaStatsClient method bound to the MetaStatsClient instance.

        Returns:
            get_account_trades MetaStatsClient method.
        """
        return self._metastats_client.get_account_trades

    @property
    def get_account_open_trades(self):
        """Returns the get_account_open_trades MetaStatsClient method bound to the MetaStatsClient instance.

        Returns:
            get_account_open_trades MetaStatsClient method.
        """
        return self._metastats_client.get_account_open_trades

    @property
    def reset_metrics(self):
        """Returns the reset_metrics MetaStatsClient method bound to the MetaStatsClient instance.

        Returns:
            reset_metrics MetaStatsClient method.
        """
        return self._metastats_client.reset_metrics

    @staticmethod
    def format_error(err: Exception):
        """Formats and outputs metaapi errors with additional information.

        Args:
            err: Exception to process.
        """
        error = {'name': err.__class__.__name__, 'message': err.args[0]}
        if hasattr(err, 'status_code'):
            error['status_code'] = err.status_code
        if err.__class__.__name__ == 'ValidationException':
            error['details'] = err.details
        if err.__class__.__name__ == 'TooManyRequestsException':
            error['metadata'] = err.metadata
        error['trace'] = traceback.format_exc()
        return error
