from typing import List, TypedDict

from .connection_registry_model import ConnectionRegistryModel
from .metatrader_account import MetatraderAccount
from .metatrader_account_replica import MetatraderAccountReplica
from ..clients.metaapi.expert_advisor_client import ExpertAdvisorClient
from ..clients.metaapi.historical_market_data_client import HistoricalMarketDataClient
from ..clients.metaapi.metaapi_websocket_client import MetaApiWebsocketClient
from ..clients.metaapi.metatrader_account_client import MetatraderAccountClient, NewMetatraderAccountDto, AccountsFilter


class MetatraderAccountList(TypedDict):
    """MetaTrader account list entity."""

    count: int
    """MetaTrader accounts count."""
    items: List[MetatraderAccount]
    """MetaTrader account entities list."""


class MetatraderAccountApi:
    """Exposes MetaTrader account API logic to the consumers."""

    def __init__(
        self,
        metatrader_account_client: MetatraderAccountClient,
        meta_api_websocket_client: MetaApiWebsocketClient,
        connection_registry: ConnectionRegistryModel,
        expert_advisor_client: ExpertAdvisorClient,
        historical_market_data_client: HistoricalMarketDataClient,
        application: str,
    ):
        """Initializes a MetaTrader account API instance.

        Args:
            metatrader_account_client: MetaTrader account REST API client.
            meta_api_websocket_client: MetaApi websocket client.
            connection_registry: MetaTrader account connection registry.
            expert_advisor_client: Expert advisor REST API client.
            historical_market_data_client: Historical market data HTTP API client.
            application: Application name.
        """
        self._metatrader_account_client = metatrader_account_client
        self._metaapi_websocket_client = meta_api_websocket_client
        self._connection_registry = connection_registry
        self._expert_advisor_client = expert_advisor_client
        self._historical_market_data_client = historical_market_data_client
        self._application = application

    async def get_accounts_with_infinite_scroll_pagination(
        self, accounts_filter: AccountsFilter = None
    ) -> List[MetatraderAccount]:
        """Returns trading accounts belonging to the current user, provides pagination in infinite scroll style.

        Args:
            accounts_filter: Optional filter.

        Returns:
            A coroutine resolving with an array of MetaTrader account entities.
        """
        if accounts_filter is None:
            accounts_filter = {}
        accounts = await self._metatrader_account_client.get_accounts(accounts_filter, '1')
        return list(
            map(
                lambda account: MetatraderAccount(
                    account,
                    self._metatrader_account_client,
                    self._metaapi_websocket_client,
                    self._connection_registry,
                    self._expert_advisor_client,
                    self._historical_market_data_client,
                    self._application,
                ),
                accounts,
            )
        )

    async def get_accounts_with_classic_scroll_pagination(
        self, accounts_filter: AccountsFilter = None
    ) -> List[MetatraderAccountList]:
        """Returns trading accounts belonging to the current user with accounts count,
        provides pagination in a classic style.

        Args:
            accounts_filter: Optional filter.

        Returns:
            A coroutine resolving with an array of MetaTrader account entities and count
        """
        if accounts_filter is None:
            accounts_filter = {}
        accounts = await self._metatrader_account_client.get_accounts(accounts_filter, '2')
        return {
            'count': len(accounts),
            'items': list(
                map(
                    lambda account: MetatraderAccount(
                        account,
                        self._metatrader_account_client,
                        self._metaapi_websocket_client,
                        self._connection_registry,
                        self._expert_advisor_client,
                        self._historical_market_data_client,
                        self._application,
                    ),
                    accounts,
                )
            ),
        }

    async def get_account(self, account_id) -> MetatraderAccount:
        """Returns trading account by id.

        Args:
            account_id: MetaTrader account id.

        Returns:
            A coroutine resolving with MetaTrader account entity.
        """
        account = await self._metatrader_account_client.get_account(account_id)
        return MetatraderAccount(
            account,
            self._metatrader_account_client,
            self._metaapi_websocket_client,
            self._connection_registry,
            self._expert_advisor_client,
            self._historical_market_data_client,
            self._application,
        )

    async def get_account_replica(self, account_id: str, replica_id: str) -> MetatraderAccountReplica:
        """Returns trading account replica by trading account id and replica id

        Args:
            account_id: MetaTrader primary account id.
            replica_id: MetaTrader account replica id.

        Returns:
            A coroutine resolving with MetaTrader account replica found.
        """
        account = await self._metatrader_account_client.get_account(account_id)
        replica = await self._metatrader_account_client.get_account_replica(account_id, replica_id)
        return MetatraderAccountReplica(replica, account, self._metatrader_account_client)

    async def get_account_replicas(self, account_id: str) -> List[MetatraderAccountReplica]:
        """Returns replicas for a trading account

        Args:
            account_id: Primary account id.

        Returns:
            A coroutine resolving with MetaTrader account replicas found.
        """
        account = await self._metatrader_account_client.get_account(account_id)
        replicas = await self._metatrader_account_client.get_account_replicas(account_id)
        if 'items' in replicas:
            replicas = replicas['items']
        return list(
            map(lambda replica: MetatraderAccountReplica(replica, account, self._metatrader_account_client), replicas)
        )

    async def create_account(self, account: NewMetatraderAccountDto) -> MetatraderAccount:
        """Adds a trading account and starts a cloud API server for the trading account.

        Args:
            account: MetaTrader account data.

        Returns:
            A coroutine resolving with an id and state of the MetaTrader account entity.
        """
        id = await self._metatrader_account_client.create_account(account)
        return await self.get_account(id['id'])
