from typing import List, Union, Optional, Dict, Literal

from httpx import Response
from typing_extensions import TypedDict

from ..metaapi_client import MetaApiClient
from ...metaapi.models import random_id

State = Literal[
    'CREATED',
    'DEPLOYING',
    'DEPLOYED',
    'DEPLOY_FAILED',
    'UNDEPLOYING',
    'UNDEPLOYED',
    'UNDEPLOY_FAILED',
    'DELETING',
    'DELETE_FAILED',
    'REDEPLOY_FAILED',
    'DRAFT',
]
"""Account state."""


ConnectionStatus = Literal['CONNECTED', 'DISCONNECTED', 'DISCONNECTED_FROM_BROKER']
"""Account connection status."""


Reliability = Literal['high', 'regular']
"""Account reliability."""


SortOrder = Literal['asc', 'desc']
"""Sort order."""


GetAccountApiVersion = Literal['1', '2']
"""Get account API version."""


Type = Literal['cloud-g1', 'cloud-g2']
"""Account type"""


Platform = Literal['mt4', 'mt5']
"""MT platform."""


Version = Literal[4, 5]
"""MT version."""


CopyFactoryRoles = Literal['SUBSCRIBER', 'PROVIDER']
"""CopyFactory roles."""

DedicatedIp = Literal['ipv4']
"""Dedicated IPs values"""


class AccountsFilter(TypedDict, total=False):
    offset: Optional[int]
    """Search offset (defaults to 0) (must be greater or equal to 0)."""
    limit: Optional[int]
    """Search limit (defaults to 1000) (must be greater or equal to 1 and less or equal to 1000)."""
    version: Optional[Union[List[int], int]]
    """MT version (allowed values are 4 and 5)"""
    type: Optional[Union[List[str], str]]
    """Account type. Allowed values are 'cloud' and 'self-hosted'"""
    state: Optional[Union[List[State], State]]
    """Account state."""
    connectionStatus: Optional[Union[List[ConnectionStatus], ConnectionStatus]]
    """Connection status."""
    query: Optional[str]
    """Searches over _id, name, server and login to match query."""
    provisioningProfileId: Optional[str]
    """Provisioning profile id."""
    sortOrder: Optional[SortOrder]
    """Sort order for stateChangedAt field, default is no order."""
    region: Optional[Union[List[str], str]]
    """Available region"""
    copyFactoryRoles: Optional[List[CopyFactoryRoles]]
    """Account roles for CopyFactory2 API."""


class MetatraderAccountIdDto(TypedDict):
    """MetaTrader account id model"""

    id: str
    """MetaTrader account unique identifier."""
    state: str
    """State of the account. Possible values are 'UNDEPLOYED', 'DEPLOYED', 'DRAFT'."""


class MetatraderAccountReplicaDto(TypedDict, total=False):
    """Metatrader account replica model"""

    _id: str
    """Unique account replica id."""
    state: State
    """Current account replica state."""
    magic: int
    """Magic value the trades should be performed using."""
    connectionStatus: ConnectionStatus
    """Connection status of the MetaTrader terminal to the application."""
    quoteStreamingIntervalInSeconds: str
    """Quote streaming interval in seconds. Set to 0 in order to receive quotes on each tick. Default value is
    2.5 seconds. Intervals less than 2.5 seconds are supported only for G2."""
    symbol: Optional[str]
    """Any symbol provided by broker (required for G1 only)."""
    reliability: Reliability
    """Used to increase the reliability of the account replica. High is a recommended value for production
    environment."""
    tags: List[str]
    """User-defined account replica tags."""
    metadata: Optional[Dict]
    """Extra information which can be stored together with your account."""
    resourceSlots: Optional[int]
    """Number of resource slots to allocate to account. Allocating extra resource slots
    results in better account performance under load which is useful for some applications. E.g. if you have many
    accounts copying the same strategy via CopyFactory API, then you can increase resourceSlots to get a lower trade
    copying latency. Please note that allocating extra resource slots is a paid option. Please note that high
    reliability accounts use redundant infrastructure, so that each resource slot for a high reliability account
    is billed as 2 standard resource slots."""
    copyFactoryResourceSlots: Optional[int]
    """Number of CopyFactory 2 resource slots to allocate to account replica.
    Allocating extra resource slots results in lower trade copying latency. Please note that allocating extra resource
    slots is a paid option. Please also note that CopyFactory 2 uses redundant infrastructure so that
    each CopyFactory resource slot is billed as 2 standard resource slots. You will be billed for CopyFactory 2
    resource slots only if you have added your account to CopyFactory 2 by specifying copyFactoryRoles field."""
    region: str
    """Region id to deploy account at. One of returned by the /users/current/regions endpoint."""
    createdAt: str
    """The time account replica was created at, in ISO format."""
    primaryAccount: dict
    """Primary account."""


class AccountConnection(TypedDict, total=False):
    """Account connection."""

    region: str
    """Region the account is connected at."""
    zone: str
    """Availability zone the account is connected at."""
    application: str
    """Application the account is connected to, one of `MetaApi`, `CopyFactory subscriber`,
    `CopyFactory provider`, `CopyFactory history import`, `Risk management`."""


class MetatraderAccountDto(TypedDict, total=False):
    """MetaTrader account model"""

    _id: str
    """Unique account id."""
    state: State
    """Current account state."""
    magic: int
    """MetaTrader magic to place trades using."""
    connectionStatus: ConnectionStatus
    """Connection status of the MetaTrader terminal to the application."""
    quoteStreamingIntervalInSeconds: str
    """Quote streaming interval in seconds. Set to 0 in order to receive quotes on each tick. Default value is
    2.5 seconds. Intervals less than 2.5 seconds are supported only for G2."""
    symbol: Optional[str]
    """Any symbol provided by broker (required for G1 only)."""
    reliability: Reliability
    """Used to increase the reliability of the account. High is a recommended value for production environment."""
    tags: Optional[List[str]]
    """User-defined account tags."""
    metadata: Optional[Dict]
    """Extra information which can be stored together with your account. Total length of this field after serializing
    it to JSON is limited to 1024 characters."""
    resourceSlots: int
    """Number of resource slots to allocate to account. Allocating extra resource slots
    results in better account performance under load which is useful for some applications. E.g. if you have many
    accounts copying the same strategy via CopyFactory API, then you can increase resourceSlots to get a lower trade
    copying latency. Please note that allocating extra resource slots is a paid option. Please note that high
    reliability accounts use redundant infrastructure, so that each resource slot for a high reliability account
    is billed as 2 standard resource slots."""
    copyFactoryResourceSlots: int
    """Number of CopyFactory 2 resource slots to allocate to account.
    Allocating extra resource slots results in lower trade copying latency. Please note that allocating extra resource
    slots is a paid option. Please also note that CopyFactory 2 uses redundant infrastructure so that
    each CopyFactory resource slot is billed as 2 standard resource slots. You will be billed for CopyFactory 2
    resource slots only if you have added your account to CopyFactory 2 by specifying copyFactoryRoles field."""
    region: str
    """Region id to deploy account at. One of returned by the /users/current/regions endpoint."""
    createdAt: str
    """The time account replica was created at, in ISO format."""
    name: str
    """Human-readable account name."""
    manualTrades: bool
    """Flag indicating if trades should be placed as manual trades. Supported on G2 only."""
    slippage: Optional[float]
    """Default trade slippage in points. Should be greater or equal to zero. If not specified, system internal setting
    will be used which we believe is reasonable for most cases."""
    provisioningProfileId: Optional[str]
    """Id of the provisioning profile that was used as the basis for creating this account."""
    login: Optional[str]
    """MetaTrader account login."""
    server: str
    """MetaTrader server name to connect to."""
    type: Type
    """Account type. Executing accounts as cloud-g2 is faster and cheaper."""
    version: Version
    """MetaTrader version."""
    hash: float
    """Hash-code of the account."""
    baseCurrency: str
    """3-character ISO currency code of the account base currency. The setting is to be used for copy trading accounts
    which use national currencies only, such as some Brazilian brokers. You should not alter this setting unless you
    understand what you are doing."""
    copyFactoryRoles: List[CopyFactoryRoles]
    """Account roles for CopyFactory2 application."""
    riskManagementApiEnabled: bool
    """Flag indicating that risk management API is enabled on account."""
    metastatsApiEnabled: bool
    """Flag indicating that MetaStats API is enabled on account."""
    allocateDedicatedIp: Optional[DedicatedIp]
    """If set, allocates a dedicated IP with specified protocol to connect to the trading account terminal. If an
    account has replicas deployed in different regions at the same time, a separate IP address will be dedicated for
    the account in each region."""
    connections: List[AccountConnection]
    """Active account connections."""
    primaryReplica: bool
    """Flag indicating that account is primary."""
    userId: str
    """User id."""
    primaryAccountId: Optional[str]
    """Primary account id. Only replicas can have this field."""
    accountReplicas: Optional[List[MetatraderAccountReplicaDto]]
    """MetaTrader account replicas."""


class MetatraderAccountListDto:
    """MetaTrader account list model."""

    count: int
    """MetaTrader accounts count."""
    items: List[MetatraderAccountDto]
    """MetaTrader accounts list."""


class NewMetatraderAccountDto(TypedDict, total=False):
    """New MetaTrader account model"""

    symbol: Optional[str]
    """Any MetaTrader symbol your broker provides historical market data for. This value should be specified for G1
    accounts only and only in case your MT account fails to connect to broker."""
    magic: int
    """Magic value the trades should be performed using. When manualTrades field is set to true, magic value
    must be 0."""
    quoteStreamingIntervalInSeconds: Optional[str]
    """Quote streaming interval in seconds. Set to 0 in order to receive quotes on each tick. Default value is 2.5
    seconds. Intervals less than 2.5 seconds are supported only for G2."""
    tags: Optional[List[str]]
    """User-defined account tags."""
    metadata: Optional[Dict]
    """Extra information which can be stored together with your account. Total length of this field after serializing
    it to JSON is limited to 1024 characters."""
    reliability: Optional[Reliability]
    """Used to increase the reliability of the account. High is a recommended value for production environment.
    Default value is high."""
    resourceSlots: Optional[int]
    """Number of resource slots to allocate to account. Allocating extra resource slots
    results in better account performance under load which is useful for some applications. E.g. if you have many
    accounts copying the same strategy via CooyFactory API, then you can increase resourceSlots to get a lower trade
    copying latency. Please note that allocating extra resource slots is a paid option. Please note that high
    reliability accounts use redundant infrastructure, so that each resource slot for a high reliability account
    is billed as 2 standard resource slots. Default is 1."""
    copyFactoryResourceSlots: Optional[int]
    """Number of CopyFactory 2 resource slots to allocate to account.
    Allocating extra resource slots results in lower trade copying latency. Please note that allocating extra resource
    slots is a paid option. Please also note that CopyFactory 2 uses redundant infrastructure so that
    each CopyFactory resource slot is billed as 2 standard resource slots. You will be billed for CopyFactory 2
    resource slots only if you have added your account to CopyFactory 2 by specifying copyFactoryRoles field.
    Default is 1."""
    region: str
    """Region id to deploy account at. One of returned by the /users/current/regions endpoint."""
    name: str
    """Human-readable account name."""
    manualTrades: Optional[bool]
    """Flag indicating if trades should be placed as manual trades. Supported on G2 only. Default is false."""
    slippage: Optional[float]
    """Default trade slippage in points. Should be greater or equal to zero. If not specified, system internal setting
    will be used which we believe is reasonable for most cases."""
    provisioningProfileId: Optional[str]
    """Id of the provisioning profile that was used as the basis for creating this account.
    Required for cloud account."""
    login: Optional[str]
    """MetaTrader account login. Only digits are allowed."""
    password: Optional[str]
    """MetaTrader account password. The password can be either investor password for read-only
    access or master password to enable trading features."""
    server: str
    """MetaTrader server name to connect to."""
    platform: Optional[Platform]
    """MetaTrader platform."""
    type: Optional[Type]
    """Account type. Executing accounts as cloud-g2 is faster and cheaper. Default value is cloud-g2."""
    baseCurrency: Optional[str]
    """3-character ISO currency code of the account base currency. Default value is USD. The setting is to be used
    for copy trading accounts which use national currencies only, such as some Brazilian
    brokers. You should not alter this setting unless you understand what you are doing."""
    copyFactoryRoles: Optional[List[CopyFactoryRoles]]
    """Account roles for CopyFactory2 API."""
    riskManagementApiEnabled: Optional[bool]
    """Flag indicating that risk management API should be enabled on account. Default is false."""
    metastatsApiEnabled: Optional[bool]
    """Flag indicating that MetaStats API is enabled on account. Default is false."""
    keywords: Optional[List[str]]
    """Keywords to be used for broker server search. We recommend to include exact broker company name in this list"""
    allocateDedicatedIp: Optional[DedicatedIp]
    """If set, allocates a dedicated IP with specified protocol to connect to the trading account terminal. If an
    account has replicas deployed in different regions at the same time, a separate IP address will be dedicated for
    the account in each region."""


class MetatraderAccountUpdateDto(TypedDict, total=False):
    """Updated MetaTrader account data"""

    name: str
    """Human-readable account name."""
    password: Optional[str]
    """MetaTrader account password. The password can be either investor password for read-only
    access or master password to enable trading features."""
    server: str
    """MetaTrader server name to connect to."""
    magic: Optional[int]
    """Magic value the trades should be performed using. When manualTrades field is set to true, magic value must
    be 0."""
    manualTrades: Optional[bool]
    """Flag indicating if trades should be placed as manual trades. Supported for G2 only. Default is false."""
    slippage: Optional[float]
    """Default trade slippage in points. Should be greater or equal to zero. If not specified,
    system internal setting will be used which we believe is reasonable for most cases."""
    quoteStreamingIntervalInSeconds: Optional[float]
    """Quote streaming interval in seconds. Set to 0 in order to receive quotes on each tick. Intervals less than 2.5
    seconds are supported only for G2. Default value is 2.5 seconds"""
    tags: Optional[List[str]]
    """MetaTrader account tags."""
    metadata: Optional[Dict]
    """Extra information which can be stored together with your account. Total length of this field after serializing
    it to JSON is limited to 1024 characters."""
    resourceSlots: Optional[int]
    """Number of resource slots to allocate to account. Allocating extra resource slots
    results in better account performance under load which is useful for some applications. E.g. if you have many
    accounts copying the same strategy via CooyFactory API, then you can increase resourceSlots to get a lower trade
    copying latency. Please note that allocating extra resource slots is a paid option. Default is 1."""
    copyFactoryResourceSlots: Optional[int]
    """Number of CopyFactory 2 resource slots to allocate to account.
    Allocating extra resource slots results in lower trade copying latency. Please note that allocating extra resource
    slots is a paid option. Please also note that CopyFactory 2 uses redundant infrastructure so that
    each CopyFactory resource slot is billed as 2 standard resource slots. You will be billed for CopyFactory 2
    resource slots only if you have added your account to CopyFactory 2 by specifying copyFactoryRoles field.
    Default is 1."""
    allocateDedicatedIp: Optional[Union[DedicatedIp, Literal["none"]]]
    """If set, allocates a dedicated IP with specified protocol to connect to the trading account terminal. If an
    account has replicas deployed in different regions at the same time, a separate IP address will be dedicated for
    the account in each region."""


class NewMetaTraderAccountReplicaDto(TypedDict, total=False):
    """New MetaTrader account replica model"""

    symbol: Optional[str]
    """Any MetaTrader symbol your broker provides historical market data for.
    This value should be specified for G1 accounts only and only in case your MT account fails to connect to broker."""
    magic: int
    """Magic value the trades should be performed using. When manualTrades field is set to true, magic value must
    be 0."""
    quoteStreamingIntervalInSeconds: Optional[str]
    """Quote streaming interval in seconds. Set to 0 in order to receive quotes on each tick. Default value is 2.5
    seconds. Intervals less than 2.5 seconds are supported only for G2."""
    tags: Optional[List[str]]
    """User-defined account replica tags."""
    metadata: Optional[Dict]
    """Extra information which can be stored together with your account. Total length of this field after serializing
    it to JSON is limited to 1024 characters."""
    reliability: Optional[Reliability]
    """Used to increase the reliability of the account replica. High is a recommended value for production environment.
    Default value is high."""
    resourceSlots: Optional[int]
    """Number of resource slots to allocate to account replica. Allocating extra resource slots
    results in better account performance under load which is useful for some applications. E.g. if you have many
    accounts copying the same strategy via CooyFactory API, then you can increase resourceSlots to get a lower trade
    copying latency. Please note that allocating extra resource slots is a paid option. Please note that high
    reliability accounts use redundant infrastructure, so that each resource slot for a high reliability account
    is billed as 2 standard resource slots. Default is 1."""
    copyFactoryResourceSlots: Optional[int]
    """Number of CopyFactory 2 resource slots to allocate to account replica.
    Allocating extra resource slots results in lower trade copying latency. Please note that allocating extra resource
    slots is a paid option. Please also note that CopyFactory 2 uses redundant infrastructure so that
    each CopyFactory resource slot is billed as 2 standard resource slots. You will be billed for CopyFactory 2
    resource slots only if you have added your account to CopyFactory 2 by specifying copyFactoryRoles field.
    Default is 1."""
    region: str
    """Region id to deploy account replica at. One of returned by the /users/current/regions endpoint."""


class UpdatedMetatraderAccountReplicaDto(TypedDict, total=False):
    """Updated MetaTrader account replica data"""

    magic: Optional[int]
    """Magic value the trades should be performed using. When manualTrades field is set to true, magic value must
    be 0."""
    quoteStreamingIntervalInSeconds: float
    """Quote streaming interval in seconds. Set to 0 in order to receive quotes on each tick. Default value is
    2.5 seconds. Intervals less than 2.5 seconds are supported only for G2."""
    tags: Optional[List[str]]
    """MetaTrader account tags."""
    metadata: Dict
    """Extra information which can be stored together with your account."""
    copyFactoryRoles: List[str]
    """Account roles for CopyFactory2 application. Allowed values are `PROVIDER` and `SUBSCRIBER`."""
    resourceSlots: Optional[int]
    """Number of resource slots to allocate to account. Allocating extra resource slots results in better account
    performance under load which is useful for some applications. E.g. if you have many accounts copying the same
    strategy via CopyFactory API, then you can increase resourceSlots to get a lower trade copying latency. Please
    note that allocating extra resource slots is a paid option. Default is 1."""
    copyFactoryResourceSlots: Optional[int]
    """Number of CopyFactory 2 resource slots to allocate to account. Allocating extra resource slots results in lower
    trade copying latency. Please note that allocating extra resource slots is a paid option. Please also note that
    CopyFactory 2 uses redundant infrastructure so that each CopyFactory resource slot is billed as 2 standard resource
    slots. You will be billed for CopyFactory 2 resource slots only if you have added your account to CopyFactory 2 by
    specifying copyFactoryRoles field. Default is 1."""


class TradingAccountCredentials(TypedDict, total=False):
    """Trading account credentials"""

    login: Optional[str]
    """Trading account login. Only digits are allowed.. Required for accounts in draft state."""
    password: str
    """Trading account password."""


class ConfigurationLink(TypedDict, total=False):
    """Configuration link."""

    configurationLink: str
    """Secure link to allow end user to configure account directly"""


class TradingAccountConfigurationInformation:
    """Trading account configuration information."""

    configured: Optional[bool]
    """Flag indicating the account has been configured."""
    login: Optional[str]
    """Trading account login."""
    server: str
    """Trading account server name."""
    platform: Platform
    """Trading account platform."""


class MetatraderAccountClient(MetaApiClient):
    """metaapi.cloud MetaTrader account API client (see https://metaapi.cloud/docs/provisioning/)

    Attributes:
        _http_client: HTTP client
        _host: domain to connect to
        _token: authorization token
    """

    async def get_accounts(
        self, accounts_filter: AccountsFilter = None, api_version: GetAccountApiVersion = None
    ) -> Response:
        """Returns trading accounts belonging to the current user
        (see https://metaapi.cloud/docs/provisioning/api/account/readAccounts/)
        Method is accessible only with API access token.

        Args:
            accounts_filter: Optional filter.
            api_version: API version to use.

        Returns:
            A coroutine resolving with Union[List[MetatraderAccountDto], MetatraderAccountListDto]
             - MetaTrader accounts found.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('get_accounts')
        opts = {
            'url': f'{self._host}/users/current/accounts',
            'method': 'GET',
            'params': accounts_filter or {},
            'headers': {'auth-token': self._token},
        }
        if api_version:
            opts['headers']['api-version'] = api_version
        return await self._http_client.request(opts, 'get_accounts')

    async def get_account(self, id: str) -> Response:
        """Returns trading account by id (see https://metaapi.cloud/docs/provisioning/api/account/readAccount/).
        Method is accessible only with API access token

        Args:
            id: MetaTrader account id.

        Returns:
            A coroutine resolving with MetatraderAccountDto - MetaTrader account found.
        """
        opts = {
            'url': f'{self._host}/users/current/accounts/{id}',
            'method': 'GET',
            'headers': {'auth-token': self._token},
        }
        return await self._http_client.request(opts, 'get_account')

    async def get_account_replica(self, account_id: str, replica_id: str) -> Response:
        """Returns trading account replica by trading account id and replica id
        (see https://metaapi.cloud/docs/provisioning/api/accountReplica/readAccountReplica/).
        Method is accessible only with API access token.

        Args:
            account_id: MetaTrader primary account id.
            replica_id: MetaTrader account replica id.

        Returns:
            A coroutine resolving with MetatraderAccountReplicaDto - MetaTrader account replica found.
        """
        opts = {
            'url': f'{self._host}/users/current/accounts/{account_id}/replicas/{replica_id}',
            'method': 'GET',
            'headers': {'auth-token': self._token},
        }
        return await self._http_client.request(opts, 'get_account_replica')

    async def get_account_replicas(self, account_id: str) -> Response:
        """Returns replicats for a trading account (see
        https://metaapi.cloud/docs/provisioning/api/accountReplica/readAccountReplicas/).
        Method is accessible only with API access token.

        Args:
            account_id: MetaTrader primary account id.

        Returns:
            A coroutine resolving with MetatraderAccountReplicaDto - MetaTrader account replica found.
        """
        opts = {
            'url': f'{self._host}/users/current/accounts/{account_id}/replicas',
            'method': 'GET',
            'headers': {'auth-token': self._token},
        }
        return await self._http_client.request(opts, 'get_account_replicas')

    async def create_account(self, account: NewMetatraderAccountDto) -> Response:
        """Adds a trading account and starts a cloud API server for the trading account
        (see https://metaapi.cloud/docs/provisioning/api/account/createAccount/).
        It can take some time for the API server and trading terminal to start and connect to broker.
        You can use the `connectionStatus` replica field to monitor the current status of the trading account.
        Method is accessible only with account access token.

        Args:
            account: MetaTrader account data.

        Returns:
            A coroutine resolving with MetatraderAccountIdDto - an id and state of the MetaTrader account created.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('create_account')
        opts = {
            'url': f'{self._host}/users/current/accounts',
            'method': 'POST',
            'headers': {'auth-token': self._token, 'transaction-id': random_id()},
            'body': account,
        }
        return await self._http_client.request(opts, 'create_account')

    async def create_account_replica(self, account_id: str, account: NewMetaTraderAccountReplicaDto) -> Response:
        """Creates a trading account replica in a region different from trading account region
        and starts a cloud API server for it.
        (see https://metaapi.cloud/docs/provisioning/api/accountReplica/createAccountReplica/).
        It can take some time for the API server and trading terminal to start and connect to broker.
        You can use the `connectionStatus` replica field to monitor the current status of the trading account.
        Method is accessible only with account access token.

        Args:
            account_id: Primary account id.
            account: MetaTrader account data.

        Returns:
            A coroutine resolving with MetatraderAccountIdDto -an id and state of the
            MetaTrader account replica created.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('create_account_replica')
        opts = {
            'url': f'{self._host}/users/current/accounts/{account_id}/replicas',
            'method': 'POST',
            'headers': {'auth-token': self._token, 'transaction-id': random_id()},
            'body': account,
        }
        return await self._http_client.request(opts, 'create_account_replica')

    async def deploy_account(self, id: str) -> Response:
        """Starts API server and trading terminal for trading account.
        This request will be ignored if the account is already been deployed.
        (see https://metaapi.cloud/docs/provisioning/api/account/deployAccount/).
        Method is accessible only with account access token.

        Args:
            id: MetaTrader account id.

        Returns:
            A coroutine resolving when MetaTrader account is scheduled for deployment
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('deploy_account')
        opts = {
            'url': f'{self._host}/users/current/accounts/{id}/deploy',
            'method': 'POST',
            'headers': {'auth-token': self._token},
        }
        return await self._http_client.request(opts, 'deploy_account')

    async def deploy_account_replica(self, account_id: str, replica_id: str) -> Response:
        """Starts API server and trading terminal for trading account replica.
        This request will be ignored if the replica is already deployed.
        (see https://metaapi.cloud/docs/provisioning/api/accountReplica/deployAccountReplica/)
        Method is accessible only with account access token.

        Args:
            account_id: MetaTrader account id.
            replica_id: MetaTrader account replica id.

        Returns:
            A coroutine resolving when MetaTrader account replica is scheduled for deployment.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('deploy_account_replica')
        opts = {
            'url': f'{self._host}/users/current/accounts/{account_id}/replicas/{replica_id}/deploy',
            'method': 'POST',
            'headers': {'auth-token': self._token},
        }
        return await self._http_client.request(opts, 'deploy_account_replica')

    async def undeploy_account(self, id: str) -> Response:
        """Stops API server and trading terminal for trading account.
        This request will be ignored if trading account is already undeployed.
        (see https://metaapi.cloud/docs/provisioning/api/account/undeployAccount/)
        Method is accessible only with account access token.

        Args:
            id: MetaTrader account id.

        Returns:
            A coroutine resolving when MetaTrader account is scheduled for undeployment.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('undeploy_account')
        opts = {
            'url': f'{self._host}/users/current/accounts/{id}/undeploy',
            'method': 'POST',
            'headers': {'auth-token': self._token},
        }
        return await self._http_client.request(opts, 'undeploy_account')

    async def undeploy_account_replica(self, account_id: str, replica_id: str) -> Response:
        """Stops API server and trading terminal for trading account replica.
        The request will be ignored if trading account replica is already undeployed.
        (see https://metaapi.cloud/docs/provisioning/api/accountReplica/undeployAccountReplica/).
        Method is accessible only with account access token.

        Args:
            account_id: MetaTrader account id to undeploy.
            replica_id: MetaTrader account replica id.

        Returns:
            A coroutine resolving when MetaTrader account replica is scheduled for undeployment.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('undeploy_account_replica')
        opts = {
            'url': f'{self._host}/users/current/accounts/{account_id}/replicas/{replica_id}/undeploy',
            'method': 'POST',
            'headers': {'auth-token': self._token},
        }
        return await self._http_client.request(opts, 'undeploy_account_replica')

    async def redeploy_account(self, id: str) -> Response:
        """Redeploys trading account. This is equivalent to undeploy immediately followed by deploy.
        (see https://metaapi.cloud/docs/provisioning/api/account/redeployAccount/)
        Method is accessible only with account access token.

        Args:
            id: MetaTrader account id.

        Returns:
            A coroutine resolving when MetaTrader account is scheduled for redeployment.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('redeploy_account')
        opts = {
            'url': f'{self._host}/users/current/accounts/{id}/redeploy',
            'method': 'POST',
            'headers': {'auth-token': self._token},
        }
        return await self._http_client.request(opts, 'redeploy_account')

    async def redeploy_account_replica(self, account_id: str, replica_id: str) -> Response:
        """Redeploys trading account replica. This is equivalent to undeploy immediately followed by deploy.
        (see https://metaapi.cloud/docs/provisioning/api/account/redeployAccountReplica/)
        Method is accessible only with account access token.

        Args:
            account_id: MetaTrader primary account id.
            replica_id: MetaTrader account replica id.

        Returns:
            A coroutine resolving when MetaTrader account replica is scheduled for redeployment.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('redeploy_account_replica')
        opts = {
            'url': f'{self._host}/users/current/accounts/{account_id}/replicas/{replica_id}/redeploy',
            'method': 'POST',
            'headers': {'auth-token': self._token},
        }
        return await self._http_client.request(opts, 'redeploy_account_replica')

    async def delete_account(self, id: str) -> Response:
        """Removes a trading account and stops the API server serving the account.
        The account state such as downloaded market data history will be removed as well when you remove the account.
        (see https://metaapi.cloud/docs/provisioning/api/account/deleteAccount/)
        Method is accessible only with account access token.

        Args:
            id: Id of the account to be deleted.

        Returns:
            A coroutine resolving when MetaTrader account is scheduled for deletion.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('delete_account')
        opts = {
            'url': f'{self._host}/users/current/accounts/{id}',
            'method': 'DELETE',
            'headers': {'auth-token': self._token},
        }
        return await self._http_client.request(opts, 'delete_account')

    async def delete_account_replica(self, account_id: str, replica_id: str) -> Response:
        """Removes a trading account replica and stops the API server serving the replica
        (see https://metaapi.cloud/docs/provisioning/api/account/deleteAccountReplica/).
        Method is accessible only with API access token.

        Args:
            account_id: Primary account id.
            replica_id: Id of the account replica to be deleted.

        Returns:
            A coroutine resolving when MetaTrader account replica is scheduled for deletion.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('delete_account_replica')
        opts = {
            'url': f'{self._host}/users/current/accounts/{account_id}/replicas/{replica_id}',
            'method': 'DELETE',
            'headers': {'auth-token': self._token},
        }
        return await self._http_client.request(opts, 'delete_account_replica')

    async def update_account(self, id: str, account: MetatraderAccountUpdateDto) -> Response:
        """Updates trading account.
        Please redeploy the trading account in order for updated settings to take effect
        (see https://metaapi.cloud/docs/provisioning/api/account/updateAccount/).
        Method is accessible only with account access token.

        Args:
            id: MetaTrader account id.
            account: Updated account information.

        Returns:
            A coroutine resolving when MetaTrader account is updated.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('update_account')
        opts = {
            'url': f'{self._host}/users/current/accounts/{id}',
            'method': 'PUT',
            'headers': {'auth-token': self._token},
            'body': account,
        }
        return await self._http_client.request(opts, 'update_account')

    async def update_account_replica(
        self, account_id: str, replica_id: str, metatrader_account: UpdatedMetatraderAccountReplicaDto
    ) -> Response:
        """Updates trading account replica (see
        https://metaapi.cloud/docs/provisioning/api/account/updateAccountReplica/).
        Method is accessible only with account access token.

        Args:
            account_id: MetaTrader primary account id.
            replica_id: MetaTrader account replica id.
            metatrader_account: Updated account replica.

        Returns:
            A coroutine resolving when MetaTrader account replica is updated.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('update_account_replica')
        opts = {
            'url': f'{self._host}/users/current/accounts/{account_id}/replicas/{replica_id}',
            'method': 'PUT',
            'headers': {'auth-token': self._token},
            'body': metatrader_account,
        }
        return await self._http_client.request(opts, 'update_account_replica')

    async def increase_reliability(self, id: str):
        """Increases trading account reliability in order to increase the expected account uptime.
        The account will be temporary stopped to perform this action.
        Note that increasing reliability is a paid option (see
        https://metaapi.cloud/docs/provisioning/api/account/increaseReliability/).
        Method is accessible only with API access token.

        Args:
            id: MetaTrader account id.

        Returns:
            A coroutine resolving when MetaTrader account reliability is increased.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('increase_reliability')
        opts = {
            'url': f'{self._host}/users/current/accounts/{id}/increase-reliability',
            'method': 'POST',
            'headers': {'auth-token': self._token},
        }
        return await self._http_client.request(opts, 'increase_reliability')

    async def enable_risk_management_api(self, id: str):
        """Enables risk management API for trading account.
        The account will be temporary stopped to perform this action.
        Note that risk management API is a paid option. (see
        https://metaapi.cloud/docs/provisioning/api/account/enableRiskManagementApi/).
        Method is accessible only with API access token.

        Args:
            id: MetaTrader account id.

        Returns:
            A coroutine resolving when account risk management is enabled.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('enable_risk_management_api')
        opts = {
            'url': f'{self._host}/users/current/accounts/{id}/enable-risk-management-api',
            'method': 'POST',
            'headers': {'auth-token': self._token},
        }
        return await self._http_client.request(opts, 'enable_risk_management_api')

    async def enable_metastats_api(self, id: str):
        """Enables MetaStats API for trading account.
        The account will be temporary stopped to perform this action.
        Note that this is a paid option. (see
        https://metaapi.cloud/docs/provisioning/api/account/enableMetaStatsApi/).
        Method is accessible only with API access token.

        Args:
            id: MetaTrader account id.

        Returns:
            A coroutine resolving when account MetaStats API is enabled.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('enable_metastats_api')
        opts = {
            'url': f'{self._host}/users/current/accounts/{id}/enable-metastats-api',
            'method': 'POST',
            'headers': {'auth-token': self._token},
        }
        return await self._http_client.request(opts, 'enable_metastats_api')

    async def create_configuration_link(self, account_id: str, ttl_in_days: int) -> ConfigurationLink:
        """Generates trading account configuration link by account id.
        (see https://metaapi.cloud/docs/provisioning/api/account/createConfigurationLink/)
        This link can be used by the end user to enter trading account login and password or change the password.
        Method is accessible only with API access token.

        Args:
            account_id: Trading account id.
            ttl_in_days: Lifetime of the link in days. Default is 7.

        Returns:
            A coroutine resolving with configuration link.
        """

        if self._is_not_jwt_token():
            return self._handle_no_access_exception('create_configuration_link')
        opts = {
            'url': f"{self._host}/users/current/accounts/{account_id}/configuration-link",
            'method': 'PUT',
            'headers': {'auth-token': self._token},
            'params': {'ttlInDays': ttl_in_days},
        }
        return await self._http_client.request(opts, 'create_configuration_link')
