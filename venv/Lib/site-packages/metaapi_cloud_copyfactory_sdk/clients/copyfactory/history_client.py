from datetime import datetime
from typing import List

from .copyfactory_models import CopyFactoryTransaction
from .streaming.transaction_listener import TransactionListener
from .streaming.transaction_listener_manager import TransactionListenerManager
from ..domain_client import DomainClient
from ..metaapi_client import MetaApiClient
from ...models import format_date, convert_iso_time_to_date


class HistoryClient(MetaApiClient):
    """metaapi.cloud CopyFactory history API (trade copying history API) client (see
    https://metaapi.cloud/docs/copyfactory/)"""

    def __init__(self, domain_client: DomainClient):
        """Initializes CopyFactory history API client instance.

        Args:
            domain_client: Domain client.
        """
        super().__init__(domain_client)
        self._domain_client = domain_client
        self._transaction_listener_manager = TransactionListenerManager(domain_client)

    async def get_provided_transactions(
        self,
        time_from: datetime,
        time_till: datetime,
        strategy_ids: List[str] = None,
        subscriber_ids: List[str] = None,
        offset: int = None,
        limit: int = None,
    ) -> 'List[CopyFactoryTransaction]':
        """Returns list of transactions on the strategies the current user provides to other users. See
        https://metaapi.cloud/docs/copyfactory/restApi/api/history/getProvidedTransactions/

        Args:
            time_from: Time to load transactions from.
            time_till: Time to load transactions till.
            strategy_ids: The list of strategy ids to filter transactions by.
            subscriber_ids: The list of CopyFactory subscriber account ids to filter by.
            offset: Pagination offset. Default value is 0.
            limit: Pagination limit. Default value is 1000.

        Returns:
            A coroutine resolving with transactions found.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('get_provided_transactions')
        params = {'from': format_date(time_from), 'till': format_date(time_till)}
        if strategy_ids:
            params['strategyId'] = strategy_ids
        if subscriber_ids:
            params['subscriberId'] = subscriber_ids
        if not (offset is None):
            params['offset'] = offset
        if limit:
            params['limit'] = limit
        opts = {
            'url': f'/users/current/provided-transactions',
            'method': 'GET',
            'headers': {'auth-token': self._token},
            'params': params,
        }
        transactions = await self._domain_client.request_copyfactory(opts, True)
        convert_iso_time_to_date(transactions)
        return transactions

    async def get_subscription_transactions(
        self,
        time_from: datetime,
        time_till: datetime,
        strategy_ids: List[str] = None,
        subscriber_ids: List[str] = None,
        offset: int = None,
        limit: int = None,
    ) -> 'List[CopyFactoryTransaction]':
        """Returns list of trades on the strategies the current user subscribed to
        https://metaapi.cloud/docs/copyfactory/restApi/api/history/getSubscriptionTransactions/

        Args:
            time_from: Time to load transactions from.
            time_till: Time to load transactions till.
            strategy_ids: The list of strategy ids to filter transactions by.
            subscriber_ids: The list of CopyFactory subscriber account ids to filter by.
            offset: Pagination offset. Default value is 0.
            limit: Pagination limit. Default value is 1000.

        Returns:
            A coroutine resolving with transactions found.
        """
        if self._is_not_jwt_token():
            return self._handle_no_access_exception('get_subscription_transactions')
        params = {'from': format_date(time_from), 'till': format_date(time_till)}
        if strategy_ids:
            params['strategyId'] = strategy_ids
        if subscriber_ids:
            params['subscriberId'] = subscriber_ids
        if not (offset is None):
            params['offset'] = offset
        if limit:
            params['limit'] = limit
        opts = {
            'url': f'/users/current/subscription-transactions',
            'method': 'GET',
            'headers': {'auth-token': self._token},
            'params': params,
        }
        transactions = await self._domain_client.request_copyfactory(opts, True)
        convert_iso_time_to_date(transactions)
        return transactions

    def add_strategy_transaction_listener(
        self, listener: TransactionListener, strategy_id: str, start_time: datetime = None
    ) -> str:
        """Adds a strategy transaction listener and creates a job to make requests.

        Args:
            listener: Transaction listener.
            strategy_id: Strategy id.
            start_time: Transaction search start time.

        Returns:
            Listener id.
        """
        return self._transaction_listener_manager.add_strategy_transaction_listener(listener, strategy_id, start_time)

    def remove_strategy_transaction_listener(self, listener_id: str):
        """Removes strategy transaction listener and cancels the event stream.

        Args:
            listener_id: Strategy transaction listener id.
        """
        self._transaction_listener_manager.remove_strategy_transaction_listener(listener_id)

    def add_subscriber_transaction_listener(
        self, listener: TransactionListener, subscriber_id: str, start_time: datetime = None
    ) -> str:
        """Adds a subscriber transaction listener and creates a job to make requests.

        Args:
            listener: Transaction listener.
            subscriber_id: Subscriber id.
            start_time: Transaction search start time.

        Returns:
            Listener id.
        """
        return self._transaction_listener_manager.add_subscriber_transaction_listener(
            listener, subscriber_id, start_time
        )

    def remove_subscriber_transaction_listener(self, listener_id: str):
        """Removes subscriber transaction listener and cancels the event stream.

        Args:
            listener_id: Subscriber transaction listener id.
        """
        self._transaction_listener_manager.remove_subscriber_transaction_listener(listener_id)
