import asyncio
import functools
from copy import copy
from datetime import datetime
from operator import itemgetter
from typing import List, Dict, Optional, Union

from typing_extensions import TypedDict

from .metatrader_account_model import MetatraderAccountModel
from .models import (
    MetatraderAccountInformation,
    MetatraderPosition,
    MetatraderOrder,
    MetatraderSymbolSpecification,
    MetatraderSymbolPrice,
    random_id,
    async_race,
)
from .terminal_hash_manager import TerminalHashManager
from ..clients.metaapi.metaapi_websocket_client import MetaApiWebsocketClient
from ..clients.metaapi.synchronization_listener import SynchronizationListener
from ..clients.timeout_exception import TimeoutException
from ..logger import LoggerManager


class TerminalStateDict(TypedDict, total=False):
    instanceIndex: Union[str, None]
    connected: bool
    connectedToBroker: bool
    accountInformation: Optional[dict]
    positions: List[dict]
    orders: List[dict]
    specificationsBySymbol: dict
    pricesBySymbol: dict
    completedOrders: dict
    removedPositions: dict
    ordersInitialized: bool
    positionsInitialized: bool
    lastSyncUpdateTime: float
    positionsHash: Union[str, None]
    ordersHash: Union[str, None]
    specificationsHash: Union[str, None]
    isSpecificationsExpected: bool
    isPositionsExcepted: bool
    isOrdersExpected: bool
    lastQuoteTime: Union[datetime, None]
    lastQuoteBrokerTime: Union[str, None]


class TerminalStateHashes(TypedDict):
    specificationsHashes: Union[List[str], None]
    positionsHashes: Union[List[str], None]
    ordersHashes: Union[List[str], None]


class RefreshTerminalStateOptions(TypedDict, total=False):
    """Options for refreshing terminal state."""

    timeoutInSeconds: Optional[float]
    """Timeout in seconds. Defaults to 10."""


class TerminalState(SynchronizationListener):
    """Responsible for storing a local copy of remote terminal state."""

    def __init__(
        self,
        account: MetatraderAccountModel,
        terminal_hash_manager: TerminalHashManager,
        websocket_client: MetaApiWebsocketClient,
    ):
        """Initializes the instance of terminal state class

        Args:
            account: MT account.
            terminal_hash_manager: Terminal hash manager.
            websocket_client: Websocket client.
        """
        super().__init__()
        self._id = random_id()
        self._account = account
        self._terminal_hash_manager = terminal_hash_manager
        self._websocket_client = websocket_client
        self._state_by_instance_index = {}
        self._wait_for_price_resolves = {}
        self._combined_instance_index = 'combined'
        self._combined_state = {
            'accountInformation': None,
            'positions': [],
            'orders': [],
            'specificationsBySymbol': None,
            'pricesBySymbol': {},
            'removedPositions': {},
            'completedOrders': {},
            'specificationsHash': None,
            'positionsHash': None,
            'ordersHash': None,
            'ordersInitialized': False,
            'positionsInitialized': False,
            'lastStatusTime': 0,
            'isSpecificationsExpected': True,
            'isPositionsExcepted': True,
            'isOrdersExpected': True,
            'lastQuoteTime': None,
            'lastQuoteBrokerTime': None,
        }
        self._process_throttled_quotes_calls = {}
        self._logger = LoggerManager.get_logger('TerminalState')

        async def check_combined_state_activity_job():
            while True:
                await asyncio.sleep(5 * 60)
                self._check_combined_state_activity_job()

        asyncio.create_task(check_combined_state_activity_job())

    @property
    def id(self):
        return self._id

    @property
    def connected(self) -> bool:
        """Returns true if MetaApi has connected to MetaTrader terminal.

        Returns:
            Whether MetaApi has connected to MetaTrader terminal.
        """
        return True in list(map(lambda instance: instance['connected'], self._state_by_instance_index.values()))

    @property
    def connected_to_broker(self) -> bool:
        """Returns true if MetaApi has connected to MetaTrader terminal and MetaTrader terminal is connected to broker

        Returns:
             Whether MetaApi has connected to MetaTrader terminal and MetaTrader terminal is connected to broker
        """
        return True in list(map(lambda instance: instance['connectedToBroker'], self._state_by_instance_index.values()))

    @property
    def account_information(self) -> MetatraderAccountInformation:
        """Returns a local copy of account information.

        Returns:
            Local copy of account information.
        """
        return self._combined_state['accountInformation']

    @property
    def positions(self) -> List[MetatraderPosition]:
        """Returns a local copy of MetaTrader positions opened.

        Returns:
            A local copy of MetaTrader positions opened.
        """
        hash = self._combined_state['positionsHash']
        return list((self._terminal_hash_manager.get_positions_by_hash(hash) or {}).values()) if hash else []

    @property
    def orders(self) -> List[MetatraderOrder]:
        """Returns a local copy of MetaTrader orders opened.

        Returns:
            A local copy of MetaTrader orders opened.
        """
        hash = self._combined_state['ordersHash']
        return list(self._terminal_hash_manager.get_orders_by_hash(hash).values()) or {} if hash else []

    @property
    def specifications(self) -> List[MetatraderSymbolSpecification]:
        """Returns a local copy of symbol specifications available in MetaTrader trading terminal.

        Returns:
             A local copy of symbol specifications available in MetaTrader trading terminal.
        """
        hash = self._combined_state['specificationsHash']
        return list(self._terminal_hash_manager.get_specifications_by_hash(hash).values()) or {} if hash else []

    def get_hashes(self) -> TerminalStateHashes:
        """Returns hashes of terminal state data for incremental synchronization.

        Returns:
            Terminal state hashes.
        """
        specifications_hashes = self._terminal_hash_manager.get_last_used_specification_hashes(self._account.server)
        positions_hashes = self._terminal_hash_manager.get_last_used_position_hashes(self._account.id)
        orders_hashes = self._terminal_hash_manager.get_last_used_order_hashes(self._account.id)

        return {
            'specificationsHashes': specifications_hashes,
            'positionsHashes': positions_hashes,
            'ordersHashes': orders_hashes,
        }

    def specification(self, symbol: str) -> Union[MetatraderSymbolSpecification, None]:
        """Returns MetaTrader symbol specification by symbol.

        Args:
            symbol: Symbol (e.g. currency pair or an index).

        Returns:
            MetatraderSymbolSpecification found or undefined if specification for a symbol is not found.
        """
        if self._combined_state['specificationsHash']:
            state = self._terminal_hash_manager.get_specifications_by_hash(self._combined_state['specificationsHash'])
            return state.get(symbol)
        else:
            return None

    def price(self, symbol: str) -> MetatraderSymbolPrice:
        """Returns MetaTrader symbol price by symbol.

        Args:
            symbol: Symbol (e.g. currency pair or an index).

        Returns:
            MetatraderSymbolPrice found or undefined if price for a symbol is not found.
        """
        return self._combined_state['pricesBySymbol'].get(symbol)

    @property
    def last_quote_time(self):
        """Returns time of the last received quote.

        Returns:
            Time of the last received quote.
        """
        if self._combined_state['lastQuoteTime']:
            return {
                'time': self._combined_state['lastQuoteTime'],
                'brokerTime': self._combined_state['lastQuoteBrokerTime'],
            }
        else:
            return None

    async def wait_for_price(self, symbol: str, timeout_in_seconds: float = 30):
        """Waits for price to be received.

        Args:
            symbol: Symbol (e.g. currency pair or an index).
            timeout_in_seconds: Timeout in seconds, default is 30.

        Returns:
            A coroutine resolving with price or undefined if price has not been received.
        """
        self._wait_for_price_resolves[symbol] = self._wait_for_price_resolves.get(symbol, [])
        if self.price(symbol) is None:
            future = asyncio.Future()
            self._wait_for_price_resolves[symbol].append(future)
            await asyncio.wait_for(future, timeout=timeout_in_seconds)

        return self.price(symbol)

    async def on_connected(self, instance_index: str, replicas: int):
        """Invoked when connection to MetaTrader terminal established.

        Args:
            instance_index: Index of an account instance connected.
            replicas: Number of account replicas launched.

        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        self._get_state(instance_index)['connected'] = True

    async def on_disconnected(self, instance_index: str):
        """Invoked when connection to MetaTrader terminal terminated.

        Args:
            instance_index: Index of an account instance connected.

        Returns:
             A coroutine which resolves when the asynchronous event is processed.
        """
        state = self._get_state(instance_index)
        state['connected'] = False
        state['connectedToBroker'] = False

    async def on_broker_connection_status_changed(self, instance_index: str, connected: bool):
        """Invoked when broker connection status have changed.

        Args:
            instance_index: Index of an account instance connected.
            connected: Is MetaTrader terminal is connected to broker.

        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        self._combined_state['lastStatusTime'] = datetime.now().timestamp()
        self._get_state(instance_index)['connectedToBroker'] = connected

    async def on_synchronization_started(
        self,
        instance_index: str,
        specifications_hash: str = None,
        positions_hash: str = None,
        orders_hash: str = None,
        synchronization_id: str = None,
    ):
        """Invoked when MetaTrader terminal state synchronization is started.

        Args:
            instance_index: Index of an account instance connected.
            specifications_hash: Specifications hash.
            positions_hash: Positions hash.
            orders_hash: Orders hash.
            synchronization_id: Synchronization id.

        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        unsynchronized_states = list(
            filter(
                lambda state_index: not self._state_by_instance_index[state_index]['ordersInitialized'],
                self._get_state_indices_of_same_instance_number(instance_index),
            )
        )
        unsynchronized_states = sorted(
            unsynchronized_states,
            key=lambda key: itemgetter('lastSyncUpdateTime')(self._state_by_instance_index[key]),
            reverse=True,
        )
        for state_index in unsynchronized_states[1:]:
            if state_index in self._state_by_instance_index:
                self._remove_state(state_index)

        state = self._get_state(instance_index)
        state['isSpecificationsExpected'] = not specifications_hash
        state['isPositionsExcepted'] = not positions_hash
        state['isOrdersExpected'] = not orders_hash
        state['lastSyncUpdateTime'] = datetime.now().timestamp()
        state['accountInformation'] = None
        state['pricesBySymbol'] = {}
        state['positions'] = []
        if not positions_hash:
            state['positionsInitialized'] = False
            state['positionsHash'] = None
        else:
            state['positionsHash'] = positions_hash
        state['orders'] = []
        if not orders_hash:
            state['ordersInitialized'] = False
            state['ordersHash'] = None
        else:
            state['ordersHash'] = orders_hash
        state['specificationsBySymbol'] = {}
        if not specifications_hash:
            self._logger.debug(
                f'{self._account.id}:{instance_index}:{synchronization_id}: cleared specifications '
                + 'on synchronization start'
            )
            state['specificationsHash'] = None
        else:
            self._logger.debug(
                f'{self._account.id}:${instance_index}:${synchronization_id}: no need to clear '
                + 'specifications on synchronization start, '
                f'{len(state["specificationsBySymbol"].keys()) if state["specificationsBySymbol"] else 0} '
                + 'specifications reused'
            )
            state['specificationsHash'] = specifications_hash

    async def on_account_information_updated(
        self, instance_index: str, account_information: MetatraderAccountInformation
    ):
        """Invoked when MetaTrader position is updated.

        Args:
            instance_index: Index of an account instance connected.
            account_information: Updated MetaTrader position.

        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        state = self._get_state(instance_index)
        self._refresh_state_update_time(instance_index)
        state['accountInformation'] = account_information
        if account_information:
            self._combined_state['accountInformation'] = copy(account_information)

    async def on_positions_replaced(self, instance_index: str, positions: List[MetatraderPosition]):
        """Invoked when the positions are replaced as a result of initial terminal state synchronization.

        Args:
            instance_index: Index of an account instance connected.
            positions: Updated array of positions.

        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        state = self._get_state(instance_index)
        self._refresh_state_update_time(instance_index)
        if state['isPositionsExcepted']:
            state['positions'] = positions

    async def on_positions_synchronized(self, instance_index: str, synchronization_id: str):
        """Invoked when position synchronization finished to indicate progress of an initial terminal state
        synchronization.

        Args:
            instance_index: Index of an account instance connected.
            synchronization_id: Synchronization request id.
        """
        state = self._get_state(instance_index)
        state['positionsInitialized'] = True

    async def on_positions_updated(
        self, instance_index: str, positions: List[MetatraderPosition], removed_positions_id: List[str]
    ):
        """Invoked when MetaTrader position are updated.

        Args:
            instance_index: Index of an account instance connected.
            positions: Updated MetaTrader positions.
            removed_positions_id: Removed positions ids.
        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        instance_state = self._get_state(instance_index)
        self._refresh_state_update_time(instance_index)
        now = datetime.now().timestamp()

        for id in removed_positions_id:
            self._combined_state['removedPositions'][id] = now

        positions = self._filter_removed_positions(positions)

        for id in list(self._combined_state['removedPositions'].keys()):
            if self._combined_state['removedPositions'][id] < now - 24 * 60 * 60:
                del self._combined_state['removedPositions'][id]

        if instance_state['ordersInitialized']:

            async def update_positions(state, instance):
                hash = self._terminal_hash_manager.update_positions(
                    self._account.id,
                    self._account.type,
                    self._id,
                    instance,
                    positions,
                    removed_positions_id,
                    state['positionsHash'],
                )
                state['positionsHash'] = hash

            await update_positions(instance_state, instance_index)
            await update_positions(self._combined_state, self._combined_instance_index)
        else:
            instance_state['positions'] = list(
                filter(lambda position: position['id'] not in removed_positions_id, instance_state['positions'])
            )

            for position in positions:
                index = None

                for i in range(len(instance_state['positions'])):
                    if instance_state['positions'][i]['id'] == position['id']:
                        index = i
                        break

                if index is None:
                    instance_state['positions'].append(position)
                else:
                    instance_state['positions'][index] = position

    async def on_pending_orders_replaced(self, instance_index: str, orders: List[MetatraderOrder]):
        """Invoked when the pending orders are replaced as a result of initial terminal state synchronization.
        This method will be invoked only if server thinks the data was updated, otherwise invocation can be skipped.

        Args:
            instance_index: Index of an account instance connected.
            orders: Updated array of pending orders.

        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        state = self._get_state(instance_index)
        self._refresh_state_update_time(instance_index)
        if state['isOrdersExpected']:
            state['orders'] = orders

    async def on_pending_orders_synchronized(self, instance_index: str, synchronization_id: str):
        """Invoked when pending order synchronization finished to indicate progress of an initial terminal state
        synchronization.

        Args:
            instance_index: Index of an account instance connected.
            synchronization_id: Synchronization request id.

        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        state = self._get_state(instance_index)
        state['positionsInitialized'] = True
        state['ordersInitialized'] = True
        self._combined_state['accountInformation'] = (
            state['accountInformation'].copy() if state.get('accountInformation') else None
        )
        state['positions'] = self._filter_removed_positions(state['positions'])

        if len(state['positions']):
            hash = self._terminal_hash_manager.record_positions(
                self._account.id, self._account.type, self._id, instance_index, state['positions']
            )
            state['positionsHash'] = hash
            self._combined_state['positions'] = [p.copy() for p in state['positions'] or []]
            self._combined_state['positionsHash'] = hash
        elif state['positionsHash']:
            self._terminal_hash_manager.remove_position_reference(self._id, instance_index)
            self._terminal_hash_manager.add_position_reference(state['positionsHash'], self._id, instance_index)
            self._combined_state['positionsHash'] = state['positionsHash']
            self._terminal_hash_manager.remove_position_reference(self._id, self._combined_instance_index)
            self._terminal_hash_manager.add_position_reference(
                state['positionsHash'], self._id, self._combined_instance_index
            )
        state['orders'] = self._filter_removed_orders(state['orders'])
        if len(state['orders']):
            hash = self._terminal_hash_manager.record_orders(
                self._account.id, self._account.type, self._id, instance_index, state['orders']
            )
            state['ordersHash'] = hash
            self._combined_state['orders'] = [o.copy() for o in state['orders'] or []]
            self._combined_state['ordersHash'] = hash
        elif state['ordersHash']:
            self._terminal_hash_manager.remove_order_reference(self._id, instance_index)
            self._terminal_hash_manager.add_order_reference(state['ordersHash'], self._id, instance_index)
            self._combined_state['ordersHash'] = state['ordersHash']
            self._terminal_hash_manager.remove_order_reference(self._id, self._combined_instance_index)
            self._terminal_hash_manager.add_order_reference(
                state['ordersHash'], self._id, self._combined_instance_index
            )
        self._logger.debug(
            f'{self._account.id}:${instance_index}:${synchronization_id}: assigned specifications to '
            + f'combined state from {instance_index}, '
            + f'{len(state["specificationsBySymbol"].keys()) if state["specificationsBySymbol"] else 0}'
            + 'specifications assigned'
        )
        self._combined_state['positionsInitialized'] = True
        self._combined_state['ordersInitialized'] = True
        if state.get('specificationsBySymbol'):
            if state['isSpecificationsExpected']:
                hash = self._terminal_hash_manager.record_specifications(
                    self._account.server,
                    self._account.type,
                    self._id,
                    instance_index,
                    list(state['specificationsBySymbol'].values()),
                )
                self._combined_state['specificationsHash'] = hash
                state['specificationsHash'] = hash
                state['specificationsBySymbol'] = None
            elif state['specificationsHash']:
                hash = self._terminal_hash_manager.update_specifications(
                    self._account.server,
                    self._account.type,
                    self._id,
                    instance_index,
                    list(state['specificationsBySymbol'].values()),
                    [],
                    state['specificationsHash'],
                )
                state['specificationsHash'] = hash
        elif state['specificationsHash']:
            self._terminal_hash_manager.remove_specification_reference(self._id, instance_index)
            self._terminal_hash_manager.add_specification_reference(
                state['specificationsHash'], self._id, instance_index
            )
            self._combined_state['specificationsHash'] = state['specificationsHash']
            self._terminal_hash_manager.remove_specification_reference(self._id, self._combined_instance_index)
            self._terminal_hash_manager.add_specification_reference(
                state['specificationsHash'], self._id, self._combined_instance_index
            )
        for state_index in self._get_state_indices_of_same_instance_number(instance_index):
            if not self._state_by_instance_index[state_index]['connected']:
                self._remove_state(state_index)

    async def on_pending_orders_updated(
        self, instance_index: str, orders: List[MetatraderOrder], completed_order_ids: List[str]
    ):
        """Invoked when MetaTrader pending orders are updated or completed

        Args:
            instance_index: Index of an account instance connected.
            orders: Updated MetaTrader pending orders.
            completed_order_ids: Completed MetaTrader pending order ids.

        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        instance_state = self._get_state(instance_index)
        self._refresh_state_update_time(instance_index)
        now = datetime.now().timestamp()

        for id in completed_order_ids:
            self._combined_state['completedOrders'][id] = now

        orders = self._filter_removed_orders(orders)

        for id in self._combined_state['completedOrders']:
            if self._combined_state['completedOrders'][id] < now - 24 * 60 * 60:
                del self._combined_state['completedOrders'][id]

        if instance_state['ordersInitialized']:

            async def update_pending_order(state, instance):
                hash = self._terminal_hash_manager.update_orders(
                    self._account.id,
                    self._account.type,
                    self._id,
                    instance,
                    orders,
                    completed_order_ids,
                    state['ordersHash'],
                )
                state['ordersHash'] = hash

            await update_pending_order(instance_state, instance_index)
            await update_pending_order(self._combined_state, self._combined_instance_index)
        else:
            instance_state['orders'] = list(
                filter(lambda order: order['id'] not in completed_order_ids, instance_state['orders'])
            )

            for order in orders:
                index = None

                for i in range(len(instance_state['orders'])):
                    if instance_state['orders'][i]['id'] == order['id']:
                        index = i
                        break

                if index is None:
                    instance_state['orders'].append(order)
                else:
                    instance_state['orders'][index] = order

    async def on_symbol_specifications_updated(
        self, instance_index: str, specifications: List[MetatraderSymbolSpecification], removed_symbols: List[str]
    ):
        """Invoked when a symbol specifications were updated.

        Args:
            instance_index: Index of an account instance connected.
            specifications: Updated MetaTrader symbol specification.
            removed_symbols: Removed symbols.

        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        instance_state = self._get_state(instance_index)
        self._refresh_state_update_time(instance_index)
        if not instance_state['ordersInitialized']:
            for specification in specifications:
                instance_state['specificationsBySymbol'][specification['symbol']] = specification
        else:
            hash = self._terminal_hash_manager.update_specifications(
                self._account.server,
                self._account.type,
                self._id,
                instance_index,
                specifications,
                removed_symbols,
                instance_state['specificationsHash'],
            )
            instance_state['specificationsHash'] = hash
            combined_hash = self._terminal_hash_manager.update_specifications(
                self._account.server,
                self._account.type,
                self._id,
                self._combined_instance_index,
                specifications,
                removed_symbols,
                self._combined_state['specificationsHash'],
            )
            self._combined_state['specificationsHash'] = combined_hash

        self._logger.debug(
            f'{self._account.id}:{instance_index}: updated {len(specifications)} specifications, '
            + f'removed {len(removed_symbols)} specifications. There are '
            + f'{len(instance_state["specificationsBySymbol"]) if instance_state["specificationsBySymbol"] else 0} '
            + 'specifications after update'
        )

    async def on_symbol_prices_updated(
        self,
        instance_index: str,
        prices: List[MetatraderSymbolPrice],
        equity: float = None,
        margin: float = None,
        free_margin: float = None,
        margin_level: float = None,
        account_currency_exchange_rate: float = None,
    ):
        """Invoked when prices for several symbols were updated.

        Args:
            instance_index: Index of an account instance connected.
            prices: Updated MetaTrader symbol prices.
            equity: Account liquidation value.
            margin: Margin used.
            free_margin: Free margin.
            margin_level: Margin level calculated as % of equity/margin.
            account_currency_exchange_rate: Current exchange rate of account currency into USD.

        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        instance_state = self._get_state(instance_index)
        self._refresh_state_update_time(instance_index)

        def update_symbol_prices(state):
            prices_initialized = False
            price_updated = False
            if prices:
                for price in prices:
                    if price['symbol'] in state['pricesBySymbol']:
                        current_price = state['pricesBySymbol'][price['symbol']]
                        if current_price['time'].timestamp() > price['time'].timestamp():
                            continue
                        else:
                            price_updated = True
                    else:
                        price_updated = True

                    if not state['lastQuoteTime'] or state['lastQuoteTime'].timestamp() < price['time'].timestamp():
                        state['lastQuoteTime'] = price.get('time')
                        state['lastQuoteBrokerTime'] = price.get('brokerTime')

                    state['pricesBySymbol'][price['symbol']] = price
                    all_positions = list(
                        (self._terminal_hash_manager.get_positions_by_hash(state['positionsHash']) or {}).values()
                    )
                    all_orders = list(
                        (self._terminal_hash_manager.get_orders_by_hash(state['ordersHash']) or {}).values()
                    )
                    positions = list(filter(lambda p: p.get('symbol') == price.get('symbol'), all_positions))
                    other_positions = list(filter(lambda p: p.get('symbol') != price.get('symbol'), all_positions))
                    orders = list(filter(lambda o: o.get('symbol') == price.get('symbol'), all_orders))
                    prices_initialized = True
                    for position in other_positions:
                        if position.get('symbol') in state['pricesBySymbol']:
                            p = state['pricesBySymbol'][position['symbol']]
                            if 'unrealizedProfit' not in position:
                                self._update_position_profits(position, p)
                        else:
                            prices_initialized = False
                    for position in positions:
                        self._update_position_profits(position, price)
                    for order in orders:
                        order['currentPrice'] = (
                            price['ask']
                            if (
                                order['type'] == 'ORDER_TYPE_BUY'
                                or order['type'] == 'ORDER_TYPE_BUY_LIMIT'
                                or order['type'] == 'ORDER_TYPE_BUY_STOP'
                                or order['type'] == 'ORDER_TYPE_BUY_STOP_LIMIT'
                            )
                            else price['bid']
                        )
                    price_resolves = (
                        self._wait_for_price_resolves[price['symbol']]
                        if price['symbol'] in self._wait_for_price_resolves
                        else []
                    )
                    if len(price_resolves):
                        resolve: asyncio.Future
                        for resolve in price_resolves:
                            if not resolve.done():
                                resolve.set_result(True)
                        del self._wait_for_price_resolves[price['symbol']]
            if price_updated and state['accountInformation']:
                positions = list(
                    (self._terminal_hash_manager.get_positions_by_hash(state['positionsHash']) or {}).values()
                )
                if state['positionsInitialized'] and prices_initialized:
                    if state['accountInformation'].get('platform') == 'mt5':
                        state['accountInformation']['equity'] = (
                            equity
                            if equity is not None
                            else state['accountInformation']['balance']
                            + functools.reduce(
                                lambda a, b: a
                                + round(
                                    (
                                        b['unrealizedProfit']
                                        if 'unrealizedProfit' in b and b['unrealizedProfit'] is not None
                                        else 0
                                    )
                                    * 100
                                )
                                / 100
                                + round((b['swap'] if 'swap' in b and b['swap'] is not None else 0) * 100) / 100,
                                positions,
                                0,
                            )
                        )
                    else:
                        state['accountInformation']['equity'] = (
                            equity
                            if equity is not None
                            else state['accountInformation']['balance']
                            + functools.reduce(
                                lambda a, b: a
                                + round((b['swap'] if 'swap' in b and b['swap'] is not None else 0) * 100) / 100
                                + round(
                                    (b['commission'] if 'commission' in b and b['commission'] is not None else 0) * 100
                                )
                                / 100
                                + round(
                                    (
                                        b['unrealizedProfit']
                                        if 'unrealizedProfit' in b and b['unrealizedProfit'] is not None
                                        else 0
                                    )
                                    * 100
                                )
                                / 100,
                                positions,
                                0,
                            )
                        )
                    state['accountInformation']['equity'] = round(state['accountInformation']['equity'] * 100) / 100
                else:
                    state['accountInformation']['equity'] = (
                        equity if equity else (state['accountInformation'].get('equity'))
                    )

                state['accountInformation']['accountCurrencyExchangeRate'] = \
                    prices[0]['accountCurrencyExchangeRate'] if 'accountCurrencyExchangeRate' in prices[0] else \
                    (state['accountInformation']['accountCurrencyExchangeRate'] if
                     'accountCurrencyExchangeRate' in state['accountInformation'] else None)
                state['accountInformation']['margin'] = (
                    margin if margin else (state['accountInformation'].get('margin'))
                )
                state['accountInformation']['freeMargin'] = (
                    free_margin
                    if free_margin
                    else (
                        state['accountInformation']['freeMargin']
                        if 'freeMargin' in state['accountInformation']
                        else None
                    )
                )
                state['accountInformation']['marginLevel'] = (
                    margin_level
                    if free_margin
                    else (
                        state['accountInformation']['marginLevel']
                        if 'marginLevel' in state['accountInformation']
                        else None
                    )
                )

        update_symbol_prices(instance_state)
        update_symbol_prices(self._combined_state)
        for price in prices:
            for call in self._process_throttled_quotes_calls.values():
                self._logger.debug(f"{self._account.id}:{instance_index}: refreshed {price['symbol']} price")
                if 'expectedSymbols' in call and price['symbol'] in call['expectedSymbols']:
                    call['expectedSymbols'].remove(price['symbol'])

                call['receivedSymbols'].add(price['symbol'])
                if not call['promise'].done() and 'expectedSymbols' in call and not len(call['expectedSymbols']):
                    call['promise'].set_result(True)

    async def on_stream_closed(self, instance_index: str):
        """Invoked when a stream for an instance index is closed.

        Args:
            instance_index: Index of an account instance connected.

        Returns:
            A coroutine which resolves when the asynchronous event is processed.
        """
        if instance_index in self._state_by_instance_index:
            for state_index in self._get_state_indices_of_same_instance_number(instance_index):
                instance_state = self._state_by_instance_index[state_index]
                if (
                    not self._state_by_instance_index[instance_index]['ordersInitialized']
                    and self._state_by_instance_index[instance_index]['lastSyncUpdateTime']
                    <= instance_state['lastSyncUpdateTime']
                ):
                    self._remove_state(instance_index)
                    break

                if instance_state['connected'] and instance_state['ordersInitialized']:
                    self._remove_state(instance_index)
                    break

    async def refresh_terminal_state(self, options: RefreshTerminalStateOptions = None):
        """Forces refresh of most recent quote updates for symbols subscribed to by the terminal, and waits for them all
        to be processed by this terminal state. This method doesn't wait for all other listeners to receive
        and process the quote updates.

        Args:
            options: Additional options.

        Returns:
            A coroutine resolving when the terminal state received and processed the latest quotes.
        """
        if options is None:
            options = {}

        call_data = {'receivedSymbols': set()}
        call_id = random_id(8)
        self._process_throttled_quotes_calls[call_id] = call_data
        call_data['promise'] = asyncio.Future()

        async def promise_timeout():
            await asyncio.sleep(options.get('timeoutInSeconds', 10))
            if call_data['promise'].done():
                return
            call_data['promise'].set_exception(TimeoutException("refreshing terminal state timed out"))

        timeout_task = asyncio.create_task(promise_timeout())

        call_data['promise'].add_done_callback(lambda f: timeout_task.cancel)

        try:
            symbols = await async_race(
                asyncio.create_task(self._websocket_client.refresh_terminal_state(self._account.id)),
                call_data['promise'],
            )
            self._logger.debug(f"{self._account.id}: expecting for {symbols if len(symbols) else 0} symbols to refresh")
            expected_symbols = set()

            for symbol in symbols:
                if symbol not in call_data['receivedSymbols']:
                    expected_symbols.add(symbol)

            call_data['expectedSymbols'] = expected_symbols
            if (
                not call_data['promise'].done()
                and 'expectedSymbols' in call_data
                and not len(call_data['expectedSymbols'])
            ):
                call_data['promise'].set_result(True)
            await call_data['promise']
        finally:
            del self._process_throttled_quotes_calls[call_id]

    def close(self):
        """Removes connection related data from terminal hash manager."""
        for instance_index in self._state_by_instance_index:
            self._remove_from_hash_manager(instance_index)
        self._remove_from_hash_manager(self._combined_instance_index)

    def _check_combined_state_activity_job(self):
        if (
            not self.connected_to_broker
            and self._combined_state['lastStatusTime'] < datetime.now().timestamp() - 30 * 60
        ):
            self._remove_from_hash_manager(self._combined_instance_index)

            self._combined_state['accountInformation'] = None
            self._combined_state['specificationsBySymbol'] = None
            self._combined_state['pricesBySymbol'] = {}
            self._combined_state['specificationsHash'] = None

            self._combined_state['orders'] = []
            self._combined_state['ordersHash'] = None

            self._combined_state['positions'] = []
            self._combined_state['positionsHash'] = None

            self._combined_state['ordersInitialized'] = False
            self._combined_state['positionsInitialized'] = False
            self._combined_state['lastStatusTime'] = 0
            self._combined_state['lastQuoteTime'] = None
            self._combined_state['lastQuoteBrokerTime'] = None

    def _remove_state(self, instance_index):
        del self._state_by_instance_index[instance_index]
        self._remove_from_hash_manager(instance_index)

    def _remove_from_hash_manager(self, instance_index):
        self._terminal_hash_manager.remove_connection_references(self._id, instance_index)

    def _refresh_state_update_time(self, instance_index: str):
        if instance_index in self._state_by_instance_index:
            state = self._state_by_instance_index[instance_index]
            if state['ordersInitialized']:
                state['lastSyncUpdateTime'] = datetime.now().timestamp()

    def _get_state_indices_of_same_instance_number(self, instance_index: str):
        region = instance_index.split(':')[0]
        instance_number = instance_index.split(':')[1]
        return list(
            filter(
                lambda state_instance_index: state_instance_index.startswith(f'{region}:{instance_number}:')
                and instance_index != state_instance_index,
                self._state_by_instance_index.keys(),
            )
        )

    def _update_position_profits(self, position: Dict, price: Dict):
        specification = self.specification(position['symbol'])
        if specification:
            multiplier = pow(10, specification.get('digits', 0))
            if 'profit' in position and position['profit'] is not None:
                position['profit'] = round(position['profit'] * multiplier) / multiplier
            if 'unrealizedProfit' not in position or 'realizedProfit' not in position:
                position['unrealizedProfit'] = (
                    (1 if (position['type'] == 'POSITION_TYPE_BUY') else -1)
                    * (position['currentPrice'] - position['openPrice'])
                    * position['currentTickValue']
                    * position['volume']
                    / specification['tickSize']
                )
                position['unrealizedProfit'] = round(position['unrealizedProfit'] * multiplier) / multiplier
                position['realizedProfit'] = position['profit'] - position['unrealizedProfit']
            new_position_price = price['bid'] if (position['type'] == 'POSITION_TYPE_BUY') else price['ask']
            is_profitable = (1 if (position['type'] == 'POSITION_TYPE_BUY') else -1) * (
                new_position_price - position['openPrice']
            )
            current_tick_value = price.get('profitTickValue') if (is_profitable > 0) else price.get('lossTickValue')
            unrealized_profit = None
            if current_tick_value is not None:
                unrealized_profit = (
                    (1 if (position['type'] == 'POSITION_TYPE_BUY') else -1)
                    * (new_position_price - position['openPrice'])
                    * current_tick_value
                    * position['volume']
                    / specification['tickSize']
                )
                unrealized_profit = round(unrealized_profit * multiplier) / multiplier

            position['unrealizedProfit'] = unrealized_profit
            if position['unrealizedProfit'] is not None and position['realizedProfit'] is not None:
                position['profit'] = position['unrealizedProfit'] + position['realizedProfit']
                position['profit'] = round(position['profit'] * multiplier) / multiplier
            position['currentPrice'] = new_position_price
            position['currentTickValue'] = current_tick_value

    def _filter_removed_positions(self, positions: List):
        return list(filter(lambda position: position['id'] not in self._combined_state['removedPositions'], positions))

    def _filter_removed_orders(self, orders: List):
        return list(filter(lambda order: order['id'] not in self._combined_state['completedOrders'], orders))

    def _get_state(self, instance_index: str) -> TerminalStateDict:
        if str(instance_index) not in self._state_by_instance_index:
            self._logger.debug(f'{self._account.id}:{instance_index}: constructed new state')
            self._state_by_instance_index[str(instance_index)] = self._construct_terminal_state(instance_index)
        return self._state_by_instance_index[str(instance_index)]

    def _construct_terminal_state(self, instance_index: str = None) -> TerminalStateDict:
        return {
            'instanceIndex': instance_index,
            'connected': False,
            'connectedToBroker': False,
            'accountInformation': None,
            'positions': [],
            'orders': [],
            'specificationsBySymbol': {},
            'pricesBySymbol': {},
            'ordersInitialized': False,
            'positionsInitialized': False,
            'lastSyncUpdateTime': 0,
            'positionsHash': None,
            'ordersHash': None,
            'specificationsHash': None,
            'isSpecificationsExpected': True,
            'isPositionsExcepted': True,
            'isOrdersExpected': True,
            'lastQuoteTime': None,
            'lastQuoteBrokerTime': None,
        }
