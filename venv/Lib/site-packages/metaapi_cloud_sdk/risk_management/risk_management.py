from typing import Optional

from typing_extensions import TypedDict

from .clients.domain_client import DomainClient
from .clients.equity_tracking.equity_tracking_client import EquityTrackingClient
from .clients.http_client import HttpClient
from .models import format_error
from ..metaapi.metaapi import MetaApi


class RetryOpts(TypedDict):
    retries: Optional[int]
    """Maximum amount of request retries, default value is 5."""
    minDelayInSeconds: Optional[float]
    """Minimum delay in seconds until request retry, default value is 1."""
    maxDelayInSeconds: Optional[float]
    """Maximum delay in seconds until request retry, default value is 30."""


class RiskManagementOptions(TypedDict):
    """Risk management SDK options."""

    domain: Optional[str]
    """Domain to connect to."""
    extendedTimeout: Optional[float]
    """Timeout for extended http requests in seconds."""
    requestTimeout: Optional[float]
    """Timeout for http requests in seconds."""
    retryOpts: Optional[RetryOpts]
    """Options for request retries."""


class RiskManagement:
    """MetaApi risk management API SDK."""

    def __init__(self, token: str, opts: RiskManagementOptions = None):
        """Initializes class instance.

        Args:
            token: Authorization token.
            opts: Connection options.
        """
        opts: RiskManagementOptions = opts or {}
        meta_api = MetaApi(token, opts)
        domain = opts.get('domain', 'agiliumtrade.agiliumtrade.ai')
        request_timeout = opts.get('requestTimeout', 10)
        request_extended_timeout = opts.get('extendedTimeout', 70)
        retry_opts = opts.get('retryOpts', {})
        http_client = HttpClient(request_timeout, request_extended_timeout, retry_opts)
        self._domain_client = DomainClient(http_client, token, 'risk-management-api-v1', domain)
        self._equity_tracking_client = EquityTrackingClient(self._domain_client, meta_api)

    @property
    def risk_management_api(self) -> EquityTrackingClient:
        """Returns CopyFactory configuration API.

        Returns:
            Configuration API.
        """
        return self._equity_tracking_client

    @staticmethod
    def format_error(err: Exception):
        """Formats and outputs metaapi errors with additional information.

        Args:
            err: Exception to process.
        """
        return format_error(err)
