import json
from hashlib import md5
from typing import Dict, List, Union

from .models import MetatraderSymbolSpecification, MetatraderPosition, MetatraderOrder, get_hash_encoder
from .reference_tree import ReferenceTree
from ..clients.metaapi.client_api_client import ClientApiClient


class TerminalHashManager:
    """Responsible for handling positions, orders, and specifications hash data."""

    def __init__(self, client_api_client: ClientApiClient, keep_hash_trees: bool = None):
        """Constructs the instance of terminal hash manager class.

        Args:
            client_api_client: Client api client.
        """
        self._client_api_client = client_api_client
        self._specifications_tree = ReferenceTree(self, 'symbol', 'specifications', True, keep_hash_trees)
        self._positions_tree = ReferenceTree(self, 'id', 'positions', False, keep_hash_trees)
        self._orders_tree = ReferenceTree(self, 'id', 'orders', False, keep_hash_trees)

    async def refresh_ignored_field_lists(self, region: str):
        await self._client_api_client.refresh_ignored_field_lists(region)

    def get_specifications_by_hash(self, specification_hash: str) -> Dict[str, MetatraderSymbolSpecification]:
        """Returns specifications data by hash.

        Args:

            specification_hash: Specification hash.
        """
        return self._specifications_tree.get_items_by_hash(specification_hash)

    def get_specifications_hashes_by_hash(self, specifications_hash: str) -> Union[Dict[str, str], None]:
        """Return specifications hash data by hash.

        Args:
            specifications_hash: Specifications hash.
        """
        return self._specifications_tree.get_hashes_by_hash(specifications_hash)

    def get_positions_by_hash(self, positions_hash: str) -> Dict[str, MetatraderPosition]:
        """Returns positions data by hash.

        Args:
            positions_hash: Positions hash.
        """
        return self._positions_tree.get_items_by_hash(positions_hash)

    def get_positions_hashes_by_hash(self, positions_hash: str) -> Union[Dict[str, str], None]:
        """Returns positions hash data by hash.

        Args:
           positions_hash: Positions hash.

        Returns:
            Dictionary of position hashes.
        """
        return self._positions_tree.get_hashes_by_hash(positions_hash)

    def get_orders_by_hash(self, orders_hash: str) -> Dict[str, MetatraderOrder]:
        """Returns orders data by hash.

        Args:
           orders_hash: Orders hash.

        Returns:
            Removed positions ids.
        """
        return self._orders_tree.get_items_by_hash(orders_hash)

    def get_orders_hashes_by_hash(self, orders_hash: str) -> Union[Dict[str, str], None]:
        """Returns orders hash data by hash.

        Args:
            orders_hash: Orders hash.

        Returns:
            Dictionary of order hashes.
        """
        return self._orders_tree.get_hashes_by_hash(orders_hash)

    def record_specifications(
        self,
        server_name: str,
        account_type: str,
        connection_id: str,
        instance_index: str,
        specifications: List[MetatraderSymbolSpecification],
    ) -> str:
        """Creates an entry for specification data and returns hash.

        Args:
            server_name: Broker server name.
            account_type: Account type.
            connection_id: Connection id.
            instance_index: Instance index.
            specifications: Specifications array.

        Returns:
            Dictionary hash.
        """
        return self._specifications_tree.record_items(
            server_name, account_type, connection_id, instance_index, specifications
        )

    def update_specifications(
        self,
        server_name: str,
        account_type: str,
        connection_id: str,
        instance_index: str,
        specifications: List[MetatraderSymbolSpecification],
        removed_symbols: List[str],
        parent_hash: str,
    ) -> str:
        """Update specifications data.

        Args:
            server_name: Broker server name.
            account_type: Account type.
            connection_id: Connection id.
            instance_index: Instance index.
            specifications: Specifications array.
            removed_symbols: Removed specifications symbols.
            parent_hash: Parent hash.

        Returns:
            Updated dictionary hash.
        """
        return self._specifications_tree.update_items(
            server_name, account_type, connection_id, instance_index, specifications, removed_symbols, parent_hash
        )

    def record_positions(
        self,
        account_id: str,
        account_type: str,
        connection_id: str,
        instance_index: str,
        positions: List[MetatraderPosition],
    ) -> str:
        """Creates an entry for positions data and returns hash

        Args:
            account_id: Account id.
            account_type: Account type.
            connection_id: Connection id.
            instance_index: Instance index.
            positions: Positions array.

        Returns:
            Dictionary hash.
        """
        return self._positions_tree.record_items(account_id, account_type, connection_id, instance_index, positions)

    def update_positions(
        self,
        account_id: str,
        account_type: str,
        connection_id: str,
        instance_index: str,
        positions: List[MetatraderPosition],
        removed_positions: List[str],
        parent_hash: str,
    ) -> str:
        """Update positions data.

        Args:
            account_id: Account id.
            account_type: Account type.
            connection_id: Connection id.
            instance_index: Instance index.
            positions: Positions.
            removed_positions: Removed positions ids.
            parent_hash: Parent hash.

        Returns:
            Updated dictionary hash.
        """
        return self._positions_tree.update_items(
            account_id, account_type, connection_id, instance_index, positions, removed_positions, parent_hash
        )

    def record_orders(
        self, account_id: str, account_type: str, connection_id: str, instance_index: str, orders: List[MetatraderOrder]
    ) -> str:
        """Creates an entry for orders data and returns hash

        Args:
            account_id: Account id.
            account_type: Account type.
            connection_id: Connection id.
            instance_index: Instance index.
            orders: Orders array.

        Returns:
            Dictionary hash.
        """
        return self._orders_tree.record_items(account_id, account_type, connection_id, instance_index, orders)

    def update_orders(
        self,
        account_id: str,
        account_type: str,
        connection_id: str,
        instance_index: str,
        orders: List[MetatraderOrder],
        completed_orders: List[str],
        parent_hash: str,
    ) -> str:
        """Updates orders data.

        Args:
            account_id: Account id.
            account_type: Account type.
            connection_id: Connection id.
            instance_index: Instance index.
            orders: Orders array.
            completed_orders: Completed orders ids.
            parent_hash: Parent hash.

        Returns:
            Updated dictionary hash.
        """
        return self._orders_tree.update_items(
            account_id, account_type, connection_id, instance_index, orders, completed_orders, parent_hash
        )

    def get_last_used_specification_hashes(self, server_name: str) -> List[str]:
        """Returns the list of last used specification hashes, with specified server hashes prioritized.

        Args:
            server_name: Server name.

        Returns:
            Last used specification hashes.
        """
        return self._specifications_tree.get_last_used_hashes(server_name)

    def get_last_used_position_hashes(self, account_id: str) -> List[str]:
        """Returns the list of last used position hashes.

        Args:
            account_id: Account id.

        Returns:
            Last used position hashes.
        """
        return self._positions_tree.get_last_used_hashes(account_id)

    def get_last_used_order_hashes(self, account_id: str) -> List[str]:
        """Returns the list of last used order hashes.

        Args:
            account_id: Account id.

        Returns:
            Last used order hashes.
        """
        return self._orders_tree.get_last_used_hashes(account_id)

    def remove_connection_references(self, connection_id: str, instance_index: str):
        """Removes all references for a connection

        Args:
            connection_id: Connection id.
            instance_index: Instance index.
        """
        self.remove_specification_reference(connection_id, instance_index)
        self.remove_position_reference(connection_id, instance_index)
        self.remove_order_reference(connection_id, instance_index)

    def add_specification_reference(self, hash: str, connection_id: str, instance_index: str):
        """Adds a reference from a terminal state instance index to a specifications hash

        Args:
            hash: Specifications hash.
            connection_id: Connection id.
            instance_index: Instance index.
        """
        self._specifications_tree.add_reference(hash, connection_id, instance_index)

    def remove_specification_reference(self, connection_id: str, instance_index: str):
        """Removes a reference from a terminal state instance index to a specifications hash

        Args:
            connection_id: Connection id.
            instance_index: Instance index.
        """
        self._specifications_tree.remove_reference(connection_id, instance_index)

    def add_position_reference(self, hash: str, connection_id: str, instance_index: str):
        """Adds a reference from a terminal state instance index to a positions hash

        Args:
            hash: Positions hash.
            connection_id: Connection id.
            instance_index: Instance index.
        """
        self._positions_tree.add_reference(hash, connection_id, instance_index)

    def remove_position_reference(self, connection_id: str, instance_index: str):
        """Removes a reference from a terminal state instance index to a position hash

        Args:
            connection_id: Connection id.
            instance_index: Instance index.
        """
        self._positions_tree.remove_reference(connection_id, instance_index)

    def add_order_reference(self, hash: str, connection_id: str, instance_index: str):
        """Adds a reference from a terminal state instance index to an order hash

        Args:
            hash: Positions hash.
            connection_id: Connection id.
            instance_index: Instance index.
        """
        self._orders_tree.add_reference(hash, connection_id, instance_index)

    def remove_order_reference(self, connection_id: str, instance_index: str):
        """Removes a reference from a terminal state instance index to an orders hash

        Args:
            connection_id: Connection id.
            instance_index: Instance index.
        """
        self._orders_tree.remove_reference(connection_id, instance_index)

    def get_item_hash(self, item: Dict, type: str, account_type: str, region: str) -> str:
        hash_fields = self._client_api_client.get_hashing_ignored_field_lists(region)
        item = item.copy()

        data = {
            'specifications': {
                'g1': hash_fields['g1']['specification'],
                'g2': hash_fields['g2']['specification'],
                'integerKeys': ['digits'],
            },
            'positions': {
                'g1': hash_fields['g1']['position'],
                'g2': hash_fields['g2']['position'],
                'integerKeys': ['magic'],
            },
            'orders': {'g1': hash_fields['g1']['order'], 'g2': hash_fields['g2']['order'], 'integerKeys': ['magic']},
        }

        account_type_keys = {'cloud-g1': 'g1', 'cloud-g2': 'g2'}

        if account_type_keys.get(account_type):
            for field in data[type][account_type_keys[account_type]]:
                if field in item:
                    del item[field]

        return self._get_hash(item, account_type, data[type]['integerKeys'])

    def _get_hash(self, obj, account_type: str, integer_keys: List[str]) -> str:
        json_item = json.dumps(obj, cls=get_hash_encoder(account_type, integer_keys), ensure_ascii=False)

        return md5(json_item.encode()).hexdigest()

    def _stop(self):
        self._specifications_tree.stop()
        self._positions_tree.stop()
        self._orders_tree.stop()
