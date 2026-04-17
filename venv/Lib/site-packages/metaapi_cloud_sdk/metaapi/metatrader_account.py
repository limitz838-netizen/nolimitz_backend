import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from .connection_registry_model import ConnectionRegistryModel
from .expert_advisor import ExpertAdvisorClient, ExpertAdvisor, NewExpertAdvisorDto
from .history_storage import HistoryStorage
from .metatrader_account_model import MetatraderAccountModel
from .metatrader_account_replica import MetatraderAccountReplica
from .models import MetatraderCandle, MetatraderTick, date
from .rpc_metaapi_connection_instance import RpcMetaApiConnectionInstance
from .streaming_metaapi_connection_instance import StreamingMetaApiConnectionInstance
from ..clients.error_handler import ValidationException
from ..clients.metaapi.historical_market_data_client import HistoricalMarketDataClient
from ..clients.metaapi.metaapi_websocket_client import MetaApiWebsocketClient
from ..clients.metaapi.metatrader_account_client import (
    MetatraderAccountClient,
    MetatraderAccountDto,
    MetatraderAccountUpdateDto,
    NewMetaTraderAccountReplicaDto,
    AccountConnection,
    MetatraderAccountReplicaDto,
    DedicatedIp
)
from ..clients.timeout_exception import TimeoutException
from ..metaapi.filesystem_history_database import FilesystemHistoryDatabase


class MetatraderAccount(MetatraderAccountModel):
    """Implements a MetaTrader account entity"""

    def __init__(
        self,
        data: MetatraderAccountDto,
        metatrader_account_client: MetatraderAccountClient,
        meta_api_websocket_client: MetaApiWebsocketClient,
        connection_registry: ConnectionRegistryModel,
        expert_advisor_client: ExpertAdvisorClient,
        historical_market_data_client: HistoricalMarketDataClient,
        application: str,
    ):
        """Initializes a MetaTrader account entity.

        Args:
            data: MetaTrader account data.
            metatrader_account_client: MetaTrader account REST API client.
            meta_api_websocket_client: MetaApi websocket client.
            connection_registry: Metatrader account connection registry.
            expert_advisor_client: Expert advisor REST API client.
            historical_market_data_client: Historical market data HTTP API client.
            application: Application name.
        """
        self._data = data
        self._metatrader_account_client = metatrader_account_client
        self._metaapi_websocket_client = meta_api_websocket_client
        self._connection_registry = connection_registry
        self._expert_advisor_client = expert_advisor_client
        self._historical_market_data_client = historical_market_data_client
        self._application = application
        self._replicas = list(
            map(
                lambda replica: MetatraderAccountReplica(replica, self, metatrader_account_client),
                data.get('accountReplicas', []),
            )
        )

    @property
    def id(self) -> str:
        """Returns unique account id.

        Returns:
            Unique account id.
        """
        return self._data['_id']

    @property
    def state(self) -> str:
        """Returns current account state. One of CREATED, DEPLOYING, DEPLOYED, DEPLOY_FAILED, UNDEPLOYING,
        UNDEPLOYED, UNDEPLOY_FAILED, DELETING, DELETE_FAILED, REDEPLOY_FAILED, DRAFT.

        Returns:
            Current account state.
        """
        return self._data['state']

    @property
    def magic(self) -> int:
        """Returns MetaTrader magic to place trades using.

        Returns:
            MetaTrader magic to place trades using.
        """
        return self._data['magic']

    @property
    def connection_status(self) -> str:
        """Returns terminal & broker connection status, one of CONNECTED, DISCONNECTED, DISCONNECTED_FROM_BROKER

        Returns:
            Terminal & broker connection status.
        """
        return self._data['connectionStatus']

    @property
    def quote_streaming_interval_in_seconds(self) -> str:
        """Returns quote streaming interval in seconds.

        Returns:
            Quote streaming interval in seconds.
        """
        return self._data['quoteStreamingIntervalInSeconds']

    @property
    def symbol(self) -> str:
        """Returns symbol provided by broker.

        Returns:
            Any symbol provided by broker.
        """
        return self._data['symbol']

    @property
    def reliability(self) -> str:
        """Returns reliability value. Possible values are regular and high.

        Returns:
            Account reliability value.
        """
        return self._data['reliability']

    @property
    def tags(self) -> List[str]:
        """Returns user-defined account tags.

        Returns:
            User-defined account tags.
        """
        return self._data.get('tags')

    @property
    def metadata(self) -> Dict:
        """Returns extra information which can be stored together with your account.

        Returns:
            Extra information which can be stored together with your account.
        """
        return self._data.get('metadata')

    @property
    def resource_slots(self) -> int:
        """Returns number of resource slots to allocate to account. Allocating extra resource slots
        results in better account performance under load which is useful for some applications. E.g. if you have many
        accounts copying the same strategy via CooyFactory API, then you can increase resourceSlots to get a lower
        trade copying latency. Please note that allocating extra resource slots is a paid option. Please note that high
        reliability accounts use redundant infrastructure, so that each resource slot for a high reliability account
        is billed as 2 standard resource slots. Default is 1.

        Returns:
            Number of resource slots to allocate to account.
        """
        return self._data.get('resourceSlots')

    @property
    def copyfactory_resource_slots(self) -> int:
        """Returns the number of CopyFactory 2 resource slots to allocate to account. Allocating extra resource slots
        results in lower trade copying latency. Please note that allocating extra resource slots is a paid option.
        Please also note that CopyFactory 2 uses redundant infrastructure so that each CopyFactory resource slot is
        billed as 2 standard resource slots. You will be billed for CopyFactory 2 resource slots only if you have
        added your account to CopyFactory 2 by specifying copyFactoryRoles field. Default is 1.

        Returns:
            Number of CopyFactory 2 resource slots to allocate to account.
        """
        return self._data.get('copyFactoryResourceSlots')

    @property
    def region(self) -> str:
        """Returns account region.

        Returns:
            Account region value.
        """
        return self._data['region']

    @property
    def created_at(self) -> datetime:
        """Returns the time account was created at, in ISO format.

        Returns:
            The time account was created at, in ISO format.
        """
        return date(self._data['createdAt'])

    @property
    def name(self) -> str:
        """Returns human-readable account name.

        Returns:
            Human-readable account name.
        """
        return self._data['name']

    @property
    def manual_trades(self) -> bool:
        """Returns flag indicating if trades should be placed as manual trades on this account.

        Returns:
            Flag indicating if trades should be placed as manual trades on this account.
        """
        return 'manualTrades' in self._data and self._data['manualTrades']

    @property
    def slippage(self) -> float:
        """Returns default trade slippage in points.

        Returns:
            Default trade slippage in points.
        """
        return 'slippage' in self._data and self._data['slippage']

    @property
    def provisioning_profile_id(self) -> str:
        """Returns id of the account's provisioning profile.

        Returns:
            ID of the account's provisioning profile.
        """
        return self._data.get('provisioningProfileId')

    @property
    def login(self) -> str:
        """Returns account login.

        Returns:
            Account login.
        """
        return self._data['login']

    @property
    def server(self) -> str:
        """Returns MetaTrader server which hosts the account.

        Returns:
            MetaTrader server which hosts the account.
        """
        return self._data['server']

    @property
    def type(self) -> str:
        """Returns account type. Possible values are cloud, cloud-g1, cloud-g2, and self-hosted.

        Returns:
            Account type.
        """
        return self._data['type']

    @property
    def version(self) -> int:
        """Returns version value. Possible values are 4 and 5.

        Returns:
            MT version.
        """
        return self._data['version']

    @property
    def hash(self) -> float:
        """Returns hash-code of the account.

        Returns:
            Hash-code of the account.
        """
        return self._data['hash']

    @property
    def base_currency(self) -> str:
        """Returns 3-character ISO currency code of the account base currency. Default value is USD. The setting is to
        be used for copy trading accounts which use national currencies only, such as some Brazilian brokers. You
        should not alter this setting unless you understand what you are doing.

        Returns:
            3-character ISO currency code of the account base currency.
        """
        return self._data.get('baseCurrency')

    @property
    def copy_factory_roles(self) -> List[str]:
        """Returns account roles for CopyFactory2 application.

        Returns:
            Account roles for CopyFactory2 application.
        """
        return self._data.get('copyFactoryRoles')

    @property
    def risk_management_api_enabled(self) -> bool:
        """Returns flag indicating that risk management API is enabled on account. Default is false.

        Returns:
            Flag indicating that risk management API is enabled on account.
        """
        return self._data['riskManagementApiEnabled']

    @property
    def metastats_api_enabled(self) -> bool:
        """Returns flag indicating that MetaStats API is enabled on account.

        Returns:
            Flag indicating that MetaStats API is enabled on account.
        """
        return self._data['metastatsApiEnabled']

    @property
    def allocate_dedicated_ip(self) -> Optional[DedicatedIp]:
        """Returns configured dedicated IP protocol to connect to the trading account terminal.

        Returns:
            Configured dedicated IP protocol to connect to the trading account terminal.
        """
        return self._data['allocateDedicatedIp']

    @property
    def connections(self) -> List[AccountConnection]:
        """Returns active account connections.

        Returns:
            Active account connections.
        """
        return self._data['connections']

    @property
    def primary_replica(self) -> bool:
        """Returns flag indicating that account is primary.

        Returns:
            Flag indicating that account is primary.
        """
        return self._data['primaryReplica']

    @property
    def user_id(self) -> str:
        """Returns user id.

        Returns:
            User id.
        """
        return self._data['userId']

    @property
    def primary_account_id(self) -> str:
        """Returns primary account id.

        Returns:
            Primary account id.
        """
        return self._data['primaryAccountId']

    @property
    def account_replicas(self) -> List[MetatraderAccountReplicaDto]:
        """Returns account replicas from DTO.

        Returns:
            Account replicas from DTO.
        """
        return self._data['accountReplicas']

    @property
    def replicas(self) -> List[MetatraderAccountReplica]:
        """Returns account replica instances.

        Returns:
            Account replica instances.
        """
        return self._replicas

    @property
    def account_regions(self) -> dict:
        """Returns a dictionary with account's available regions and replicas.

        Returns:
            A dictionary with account's available regions and replicas.
        """
        regions = {self.region: self.id}
        for replica in self.replicas:
            regions[replica.region] = replica.id

        return regions

    async def reload(self):
        """Reloads MetaTrader account from API.

        Returns:
            A coroutine resolving when MetaTrader account is updated.
        """
        self._data = await self._metatrader_account_client.get_account(self.id)
        updated_replica_data = self._data.get('accountReplicas', [])
        regions = list(map(lambda replica: replica['region'], updated_replica_data))
        created_replica_regions = list(map(lambda replica: replica.region, self._replicas))
        self._replicas = list(filter(lambda replica: replica.region in regions, self._replicas))
        for replica in self._replicas:
            updated_data = next(
                (replica_data for replica_data in updated_replica_data if (replica_data['region'] == replica.region)),
                None,
            )
            replica.update_data(updated_data)
        for replica in updated_replica_data:
            if replica['region'] not in created_replica_regions:
                self._replicas.append(MetatraderAccountReplica(replica, self, self._metatrader_account_client))

    async def remove(self):
        """Removes a trading account and stops the API server serving the account.
        The account state such as downloaded market data history will be removed as well when you remove the account.

        Returns:
            A coroutine resolving when account is scheduled for deletion.
        """
        self._connection_registry.remove(self.id)
        await self._metatrader_account_client.delete_account(self.id)
        file_manager = FilesystemHistoryDatabase.get_instance()
        await file_manager.clear(self.id, self._application)
        if self.type != 'self-hosted':
            try:
                await self.reload()
            except Exception as err:
                if err.__class__.__name__ != 'NotFoundException':
                    raise err

    async def deploy(self):
        """Starts API server and trading terminal for trading account.
        This request will be ignored if the account is already deployed.

        Returns:
            A coroutine resolving when account is scheduled for deployment.
        """
        await self._metatrader_account_client.deploy_account(self.id)
        await self.reload()

    async def undeploy(self):
        """Stops API server and trading terminal for trading account.
        This request will be ignored if treading account is already undeployed.

        Returns:
            A coroutine resolving when account is scheduled for undeployment.
        """
        self._connection_registry.remove(self.id)
        await self._metatrader_account_client.undeploy_account(self.id)
        await self.reload()

    async def redeploy(self):
        """Redeploys trading account. This is equivalent to undeploy immediately followed by deploy.

        Returns:
            A coroutine resolving when account is scheduled for redeployment.
        """
        await self._metatrader_account_client.redeploy_account(self.id)
        await self.reload()

    async def increase_reliability(self):
        """Increases MetaTrader account reliability. The account will be temporary stopped to perform this action.

        Returns:
            A coroutine resolving when account reliability is increased.
        """
        await self._metatrader_account_client.increase_reliability(self.id)
        await self.reload()

    async def enable_risk_management_api(self):
        """Enables risk management API for trading account.
        The account will be temporary stopped to perform this action.
        Note that risk management API is a paid option.

        Returns:
            A coroutine resolving when account risk management is enabled.
        """
        await self._metatrader_account_client.enable_risk_management_api(self.id)
        await self.reload()

    async def enable_metastats_api(self):
        """Enables MetaStats API for trading account.
        The account will be temporary stopped to perform this action.
        Not that this is a paid option.

        Returns:
            A coroutine resolving when account MetaStats API is enabled.
        """
        await self._metatrader_account_client.enable_metastats_api(self.id)
        await self.reload()

    async def wait_deployed(self, timeout_in_seconds=300, interval_in_milliseconds=1000):
        """Waits until API server has finished deployment and account reached the DEPLOYED state.

        Args:
            timeout_in_seconds: Wait timeout in seconds, default is 5m.
            interval_in_milliseconds: Interval between account reloads while waiting for a change, default is 1s.

        Returns:
            A coroutine which resolves when account is deployed.

        Raises:
            TimeoutException: If account has not reached the DEPLOYED state within timeout allowed.
        """
        start_time = datetime.now()
        await self.reload()
        while self.state != 'DEPLOYED' and (start_time + timedelta(seconds=timeout_in_seconds) > datetime.now()):
            await self._delay(interval_in_milliseconds)
            await self.reload()
        if self.state != 'DEPLOYED':
            raise TimeoutException('Timed out waiting for account ' + self.id + ' to be deployed')

    async def wait_undeployed(self, timeout_in_seconds=300, interval_in_milliseconds=1000):
        """Waits until API server has finished undeployment and account reached the UNDEPLOYED state.

        Args:
            timeout_in_seconds: Wait timeout in seconds, default is 5m.
            interval_in_milliseconds: Interval between account reloads while waiting for a change, default is 1s.

        Returns:
            A coroutine which resolves when account is undeployed.

        Raises:
            TimeoutException: If account have not reached the UNDEPLOYED state within timeout allowed.
        """
        start_time = datetime.now()
        await self.reload()
        while self.state != 'UNDEPLOYED' and (start_time + timedelta(seconds=timeout_in_seconds) > datetime.now()):
            await self._delay(interval_in_milliseconds)
            await self.reload()
        if self.state != 'UNDEPLOYED':
            raise TimeoutException('Timed out waiting for account ' + self.id + ' to be undeployed')

    async def wait_removed(self, timeout_in_seconds=300, interval_in_milliseconds=1000):
        """Waits until account has been deleted.

        Args:
            timeout_in_seconds: Wait timeout in seconds, default is 5m.
            interval_in_milliseconds: Interval between account reloads while waiting for a change, default is 1s.

        Returns:
            A coroutine which resolves when account is deleted.

        Raises:
            TimeoutException: If account was not deleted within timeout allowed.
        """
        start_time = datetime.now()
        try:
            await self.reload()
            while (start_time + timedelta(seconds=timeout_in_seconds)) > datetime.now():
                await self._delay(interval_in_milliseconds)
                await self.reload()
            raise TimeoutException('Timed out waiting for account ' + self.id + ' to be deleted')
        except Exception as err:
            if err.__class__.__name__ == 'NotFoundException':
                return
            else:
                raise err

    async def wait_connected(self, timeout_in_seconds=300, interval_in_milliseconds=1000):
        """Waits until API server has connected to the terminal and terminal has connected to the broker.

        Args:
            timeout_in_seconds: Wait timeout in seconds, default is 5m
            interval_in_milliseconds: Interval between account reloads while waiting for a change, default is 1s.

        Returns:
            A coroutine which resolves when API server is connected to the broker.

        Raises:
            TimeoutException: If account has not connected to the broker within timeout allowed.
        """

        def check_connected():
            return 'CONNECTED' in [self.connection_status] + list(
                map(lambda replica: replica.connection_status, self.replicas)
            )

        start_time = datetime.now()
        await self.reload()
        while not check_connected() and (start_time + timedelta(seconds=timeout_in_seconds)) > datetime.now():
            await self._delay(interval_in_milliseconds)
            await self.reload()
        if not check_connected():
            raise TimeoutException('Timed out waiting for account ' + self.id + ' to connect to the broker')

    def get_streaming_connection(
        self, history_storage: HistoryStorage = None, history_start_time: datetime = None
    ) -> StreamingMetaApiConnectionInstance:
        """Connects to MetaApi via streaming connection instance.

        Args:
            history_storage: Optional history storage.
            history_start_time: History start time. Used for tests.

        Returns:
            MetaApi connection.
        """
        if self._metaapi_websocket_client.region and self._metaapi_websocket_client.region != self.region:
            raise ValidationException(
                f'Account {self.id} is not on specified region ' f'{self._metaapi_websocket_client.region}'
            )
        return self._connection_registry.connect_streaming(self, history_storage, history_start_time)

    def get_rpc_connection(self) -> RpcMetaApiConnectionInstance:
        """Connects to MetaApi via RPC connection instance.

        Returns:
            MetaApi connection.
        """
        if self._metaapi_websocket_client.region and self._metaapi_websocket_client.region != self.region:
            raise ValidationException(
                f'Account {self.id} is not on specified region ' f'{self._metaapi_websocket_client.region}'
            )
        return self._connection_registry.connect_rpc(self)

    async def update(self, account: MetatraderAccountUpdateDto):
        """Updates MetaTrader account data.

        Args:
            account: MetaTrader account update.

        Returns:
            A coroutine resolving when account is updated.
        """
        await self._metatrader_account_client.update_account(self.id, account)
        await self.reload()

    async def create_replica(self, account: NewMetaTraderAccountReplicaDto) -> MetatraderAccountReplica:
        """Creates a trading account replica in a region different from trading account region
        and starts a cloud API server for it.

        Args:
            account: MetaTrader account replica data.

        Returns:
            A coroutine resolving with an id and state of the MetaTrader account replica entity.
        """
        await self._metatrader_account_client.create_account_replica(self.id, account)
        await self.reload()
        return next((r for r in self._replicas if r.region == account['region']), None)

    async def get_expert_advisors(self) -> List[ExpertAdvisor]:
        """Retrieves expert advisors of current account.

        Returns:
            A coroutine resolving with an array of expert advisor entities.
        """
        self._check_expert_advisor_allowed()
        expert_advisors = await self._expert_advisor_client.get_expert_advisors(self.id)
        return list(map(lambda e: ExpertAdvisor(e, self.id, self._expert_advisor_client), expert_advisors))

    async def get_expert_advisor(self, expert_id: str) -> ExpertAdvisor:
        """Retrieves an expert advisor of current account by id.

        Args:
            expert_id: Expert advisor id.

        Returns:
            A coroutine resolving with expert advisor entity.
        """
        self._check_expert_advisor_allowed()
        expert_advisor = await self._expert_advisor_client.get_expert_advisor(self.id, expert_id)
        return ExpertAdvisor(expert_advisor, self.id, self._expert_advisor_client)

    async def create_expert_advisor(self, expert_id: str, expert: NewExpertAdvisorDto) -> ExpertAdvisor:
        """Creates an expert advisor.

        Args:
            expert_id: Expert advisor id.
            expert: Expert advisor data.

        Returns:
            A coroutine resolving with expert advisor entity.
        """
        self._check_expert_advisor_allowed()
        await self._expert_advisor_client.update_expert_advisor(self.id, expert_id, expert)
        return await self.get_expert_advisor(expert_id)

    async def get_historical_candles(
        self, symbol: str, timeframe: str, start_time: datetime = None, limit: int = None
    ) -> List[MetatraderCandle]:
        """Returns historical candles for a specific symbol and timeframe from a MetaTrader account.
        See https://metaapi.cloud/docs/client/restApi/api/retrieveMarketData/readHistoricalCandles/

        Args:
            symbol: Symbol to retrieve candles for (e.g. a currency pair or an index).
            timeframe: Defines the timeframe according to which the candles must be generated. Allowed values
            for MT5 are 1m, 2m, 3m, 4m, 5m, 6m, 10m, 12m, 15m, 20m, 30m, 1h, 2h, 3h, 4h, 6h, 8h, 12h, 1d, 1w, 1mn.
            Allowed values for MT4 are 1m, 5m, 15m 30m, 1h, 4h, 1d, 1w, 1mn.
            start_time: Time to start loading candles from. Note that candles are loaded in backwards direction, so
            this should be the latest time. Leave empty to request latest candles.
            limit: Maximum number of candles to retrieve. Must be less or equal to 1000.

        Returns:
            A coroutine resolving with historical candles downloaded.
        """
        return await self._historical_market_data_client.get_historical_candles(
            self.id, self.region, symbol, timeframe, start_time, limit
        )

    async def get_historical_ticks(
        self, symbol: str, start_time: datetime = None, offset: int = None, limit: int = None
    ) -> List[MetatraderTick]:
        """Returns historical ticks for a specific symbol from a MetaTrader account.
        See https://metaapi.cloud/docs/client/restApi/api/retrieveMarketData/readHistoricalTicks/

        Args:
            symbol: Symbol to retrieve ticks for (e.g. a currency pair or an index).
            start_time: Time to start loading ticks from. Note that ticks are loaded in backwards direction, so
            this should be the latest time. Leave empty to request latest ticks.
            offset: Number of ticks to skip (you can use it to avoid requesting ticks from previous request twice)
            limit: Maximum number of ticks to retrieve. Must be less or equal to 1000.

        Returns:
            A coroutine resolving with historical ticks downloaded.
        """
        return await self._historical_market_data_client.get_historical_ticks(
            self.id, self.region, symbol, start_time, offset, limit
        )

    async def create_configuration_link(self, ttl_in_days: int):
        """Generates trading account configuration link by account id.

        Args:
            ttl_in_days: Lifetime of the link in days. Default is 7.

        Returns:
            A coroutine resolving with configuration link.
        """
        return await self._metatrader_account_client.create_configuration_link(self.id, ttl_in_days)

    def _check_expert_advisor_allowed(self):
        if self.version != 4 or self.type != 'cloud-g1':
            raise ValidationException('Custom expert advisor is available only for MT4 G1 accounts')

    async def _delay(self, timeout_in_milliseconds):
        await asyncio.sleep(timeout_in_milliseconds / 1000)
