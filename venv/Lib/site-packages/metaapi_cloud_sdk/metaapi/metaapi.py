import asyncio
import re
from typing import Optional

from typing_extensions import TypedDict

from .latency_monitor import LatencyMonitor
from .metatrader_account_generator_api import MetatraderAccountGeneratorApi
from .terminal_hash_manager import TerminalHashManager
from ..clients.domain_client import DomainClient
from ..clients.error_handler import ValidationException
from ..clients.http_client import HttpClient
from ..clients.metaapi.client_api_client import ClientApiClient
from ..clients.metaapi.expert_advisor_client import ExpertAdvisorClient
from ..clients.metaapi.historical_market_data_client import HistoricalMarketDataClient
from ..clients.metaapi.metaapi_websocket_client import MetaApiWebsocketClient
from ..clients.metaapi.metatrader_account_client import MetatraderAccountClient
from ..clients.metaapi.metatrader_account_generator_client import MetatraderAccountGeneratorClient
from ..clients.metaapi.packet_logger import PacketLoggerOpts
from ..clients.metaapi.provisioning_profile_client import ProvisioningProfileClient
from ..clients.metaapi.synchronization_throttler import SynchronizationThrottlerOpts
from ..clients.metaapi.token_management_client import TokenManagementClient
from ..clients.options_validator import OptionsValidator
from ..logger import LoggerManager
from ..metaapi.connection_registry import ConnectionRegistry
from ..metaapi.metatrader_account_api import MetatraderAccountApi
from ..metaapi.models import format_error, RetryOpts
from ..metaapi.provisioning_profile_api import ProvisioningProfileApi
from ..metaapi.token_management_api import TokenManagementApi


class RefreshSubscriptionsOpts(TypedDict, total=False):
    """Subscriptions refresh options."""

    minDelayInSeconds: Optional[float]
    """Minimum delay in seconds until subscriptions refresh request, default value is 1."""
    maxDelayInSeconds: Optional[float]
    """Maximum delay in seconds until subscriptions refresh request, default value is 600."""


class MetaApiOpts(TypedDict, total=False):
    """MetaApi options"""

    application: Optional[str]
    """Application id."""
    domain: Optional[str]
    """Domain to connect to, default is agiliumtrade.agiliumtrade.ai."""
    region: Optional[str]
    """Region to connect to."""
    requestTimeout: Optional[float]
    """Timeout for socket requests in seconds."""
    connectTimeout: Optional[float]
    """Timeout for connecting to server in seconds."""
    packetOrderingTimeout: Optional[float]
    """Packet ordering timeout in seconds."""
    historicalMarketDataRequestTimeout: Optional[float]
    """Timeout for historical market data client in seconds."""
    accountGeneratorRequestTimeout: Optional[float]
    """Timeout for account generator requests in seconds. Default is 4 minutes."""
    packetLogger: Optional[PacketLoggerOpts]
    """Packet logger options."""
    enableLatencyMonitor: Optional[bool]
    """An option to enable latency tracking."""
    enableLatencyTracking: Optional[bool]
    """An option to enable latency tracking."""
    synchronizationThrottler: Optional[SynchronizationThrottlerOpts]
    """Options for synchronization throttler."""
    retryOpts: Optional[RetryOpts]
    """Options for request retries."""
    useSharedClientApi: Optional[bool]
    """Option to use a shared server."""
    enableSocketioDebugger: Optional[bool]
    """Option to enable debug mode."""
    refreshSubscriptionsOpts: Optional[RefreshSubscriptionsOpts]
    """Subscriptions refresh options."""
    unsubscribeThrottlingIntervalInSeconds: Optional[float]
    """A timeout in seconds for throttling repeat unsubscribe
    requests when synchronization packets still arrive after unsubscription, default is 10 seconds"""
    keepHashTrees: Optional[bool]
    """If set to true, unused data will not be cleared (for use in debugging)."""
    websocketLogPath: Optional[str]
    """You can record detailed web socket logs to diagnose your problem to a log file. In order to do so,
    please specify the path to socket.io log filename in this setting"""


class MetaApi:
    """MetaApi MetaTrader API SDK"""

    def __init__(self, token: str, opts: MetaApiOpts = None):
        """Initializes MetaApi class instance.

        Args:
            token: Authorization token.
            opts: Application options.
        """
        validator = OptionsValidator()
        opts: MetaApiOpts = {k: opts[k] for k in opts if k != 'connections'} if opts else {}
        application = opts.get('application', 'MetaApi')
        domain = opts.get('domain', 'agiliumtrade.agiliumtrade.ai')
        region = opts.get('region')
        unsubscribe_throttling_interval_in_seconds = opts.get('unsubscribeThrottlingIntervalInSeconds')
        request_timeout = validator.validate_non_zero(opts.get('requestTimeout'), 60, 'requestTimeout')
        historical_market_data_request_timeout = validator.validate_non_zero(
            opts.get('historicalMarketDataRequestTimeout'), 240, 'historicalMarketDataRequestTimeout'
        )
        connect_timeout = validator.validate_non_zero(opts.get('connectTimeout'), 60, 'connectTimeout')
        packet_ordering_timeout = validator.validate_non_zero(
            opts.get('packetOrderingTimeout'), 60, 'packetOrderingTimeout'
        )
        retry_opts = opts.get('retryOpts', {})
        packet_logger = opts.get('packetLogger', {})
        synchronization_throttler = opts.get('synchronizationThrottler', {})
        account_generator_request_timeout = validator.validate_non_zero(
            opts.get('accountGeneratorRequestTimeout'), 240, 'accountGeneratorRequestTimeout'
        )
        use_shared_client_api = opts.get('useSharedClientApi', False)
        enable_socketio_debugger = opts.get('enableSocketioDebugger', False)
        websocket_log_path = opts.get('websocketLogPath', './.metaapi/logs/socket.log')
        refresh_subscriptions_opts = opts.get('refreshSubscriptionsOpts', {})
        if not re.search(r"[a-zA-Z0-9_]+", application):
            raise ValidationException(
                'Application name must be non-empty string consisting ' + 'from letters, digits and _ only'
            )
        http_client = HttpClient(request_timeout, retry_opts)
        domain_client = DomainClient(http_client, token, domain)
        historical_market_data_http_client = HttpClient(historical_market_data_request_timeout, retry_opts)
        account_generator_http_client = HttpClient(account_generator_request_timeout, retry_opts)
        client_api_client = ClientApiClient(http_client, domain_client)
        self._terminal_hash_manager = TerminalHashManager(client_api_client, opts.get('keepHashTrees'))
        token_management_client = TokenManagementClient(http_client, domain_client)
        self._metaapi_websocket_client = MetaApiWebsocketClient(
            self,
            domain_client,
            token,
            {
                'application': application,
                'domain': domain,
                'requestTimeout': request_timeout,
                'connectTimeout': connect_timeout,
                'packetLogger': packet_logger,
                'region': region,
                'packetOrderingTimeout': packet_ordering_timeout,
                'synchronizationThrottler': synchronization_throttler,
                'retryOpts': retry_opts,
                'useSharedClientApi': use_shared_client_api,
                'enableSocketioDebugger': enable_socketio_debugger,
                'unsubscribeThrottlingIntervalInSeconds': unsubscribe_throttling_interval_in_seconds,
                'websocketLogPath': websocket_log_path
            },
        )
        self._provisioning_profile_api = ProvisioningProfileApi(ProvisioningProfileClient(http_client, domain_client))
        self._connection_registry = ConnectionRegistry(
            opts, self._metaapi_websocket_client, self._terminal_hash_manager, application, refresh_subscriptions_opts
        )
        historical_market_data_client = HistoricalMarketDataClient(historical_market_data_http_client, domain_client)
        self._metatrader_account_api = MetatraderAccountApi(
            MetatraderAccountClient(http_client, domain_client),
            self._metaapi_websocket_client,
            self._connection_registry,
            ExpertAdvisorClient(http_client, domain_client),
            historical_market_data_client,
            application,
        )
        self._metatrader_account_generator_api = MetatraderAccountGeneratorApi(
            MetatraderAccountGeneratorClient(account_generator_http_client, domain_client)
        )
        self._token_management_api = TokenManagementApi(token_management_client)
        if ('enableLatencyTracking' in opts and opts['enableLatencyTracking']) or (
            'enableLatencyMonitor' in opts and opts['enableLatencyMonitor']
        ):
            self._latency_monitor = LatencyMonitor()
            self._metaapi_websocket_client.add_latency_listener(self._latency_monitor)

    @staticmethod
    def enable_logging():
        """Enables using Logging logger with extended log levels for debugging instead of
        print function. Note that Logging configuration is performed by the user."""
        LoggerManager.use_logging()

    @property
    def provisioning_profile_api(self) -> ProvisioningProfileApi:
        """Returns provisioning profile API.

        Returns:
            Provisioning profile API.
        """
        return self._provisioning_profile_api

    @property
    def metatrader_account_api(self) -> MetatraderAccountApi:
        """Returns MetaTrader account API.

        Returns:
            MetaTrader account API.
        """
        return self._metatrader_account_api

    @property
    def metatrader_account_generator_api(self) -> MetatraderAccountGeneratorApi:
        """Returns MetaTrader account generator API.

        Returns:
            MetaTrader account generator API.
        """
        return self._metatrader_account_generator_api

    @property
    def latency_monitor(self) -> LatencyMonitor:
        """Returns MetaApi application latency monitor.

        Returns:
            Latency monitor.
        """
        return self._latency_monitor if hasattr(self, '_latency_monitor') else None

    @property
    def token_management_api(self) -> TokenManagementApi:
        """Returns token management API.

        Returns:
            Token management API.
        """
        return self._token_management_api

    @staticmethod
    def format_error(err: Exception):
        """Formats and outputs metaapi errors with additional information.

        Args:
            err: Exception to process.
        """
        return format_error(err)

    def close(self):
        """Closes all clients and connections and stops all internal jobs"""
        if hasattr(self, '_latency_monitor'):
            self._metaapi_websocket_client.remove_latency_listener(self._latency_monitor)
        asyncio.create_task(self._metaapi_websocket_client.close())
        self._metaapi_websocket_client.stop()
        self._terminal_hash_manager._stop()
