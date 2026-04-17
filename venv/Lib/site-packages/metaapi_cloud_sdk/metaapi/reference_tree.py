import asyncio
import functools
from contextlib import suppress
from datetime import datetime
from typing import Dict, Union, List

import rapidfuzz


class ReferenceTree:
    # terminal_hash_manager typing removed due to circular reference
    def __init__(
        self,
        terminal_hash_manager,
        id_key: str,
        data_type: str,
        use_fuzzy_search: bool = None,
        keep_hash_trees: bool = None,
    ):
        """Initializes the instance of reference tree.

        Args:
            terminal_hash_manager: Terminal hash manager.
            id_key: Field name that contains the item id.
            data_type: Data type.
            use_fuzzy_search: Whether to use fuzzy search on nearby categories.
            keep_hash_trees: If set to True, used data will not be cleared (for use in debugging).
        """
        self._terminal_hash_manager = terminal_hash_manager
        self._id_key = id_key
        self._data_by_hash = {}
        self._hashes_by_category = {}
        self._data_type = data_type
        self._use_fuzzy_search = use_fuzzy_search or False
        self._record_expiration_time = 10 * 60

        if not keep_hash_trees:

            async def optimize_trees_job():
                while True:
                    await asyncio.sleep(5 * 60)
                    self._optimize_trees_job()

            self._interval = asyncio.create_task(optimize_trees_job())

    def get_items_by_hash(self, hash: str) -> Union[Dict[str, Dict], None]:
        """Returns data by hash.

        Args:
            hash: Records hash.
        """
        data = self._data_by_hash.get(hash)
        if not data:
            return None
        elif not data.get("parentHash"):
            return data.get("data")
        else:
            #             If specified hash is not a base hash, build a chain of hashes
            # to the base one, apply all changes and return
            hash_chain = [hash]
            hash_chain.insert(0, data["parentHash"])
            parent_data = self._data_by_hash.get(data["parentHash"])

            while parent_data.get("parentHash"):
                hash_chain.insert(0, parent_data.get("parentHash"))
                parent_data = self._data_by_hash.get(parent_data.get("parentHash"))

            state = self._data_by_hash[hash_chain.pop(0)]["data"].copy()

            for chain_hash in hash_chain:
                chain_data = self._data_by_hash[chain_hash]
                for id in chain_data["data"]:
                    state[id] = chain_data["data"][id]
                for id in chain_data["removedItemIds"]:
                    if id in state:
                        del state[id]

            return state

    def get_hashes_by_hash(self, hash: str) -> Union[Dict[str, str], None]:
        """Returns hash data by hash.

        Args:
            hash: Records hash.
        """
        data = self._data_by_hash.get(hash)
        if not data:
            return None
        elif not data.get("parentHash"):
            return data.get("hashes")
        else:
            #             If specified hash is not a base hash, build a chain of hashes
            # to the base one, apply all changes and return
            hash_chain = [hash]
            hash_chain.insert(0, data["parentHash"])
            parent_data = self._data_by_hash.get(data["parentHash"])

            while parent_data.get("parentHash"):
                hash_chain.insert(0, parent_data.get("parentHash"))
                parent_data = self._data_by_hash.get(parent_data.get("parentHash"))
            state = self._data_by_hash[hash_chain.pop(0)]["hashes"].copy()

            for chain_hash in hash_chain:
                chain_data = self._data_by_hash[chain_hash]
                for id in chain_data["hashes"]:
                    state[id] = chain_data["hashes"][id]
                for id in chain_data["removedItemIds"]:
                    if id in state:
                        del state[id]

            return state

    def record_items(
        self, category_name: str, account_type: str, connection_id: str, instance_index: str, items: List
    ) -> Union[str, None]:
        """Creates an entry for data and returns hash.

        Args:
            category_name: Category name.
            account_type: Account type.
            connection_id: Connection id.
            instance_index: Instance index.
            items: Items to record.

        Returns:
            Data hash.
        """
        region = instance_index.split(":")[0]
        hash_dictionary = {}
        data_dictionary = {}

        if not items:
            return None

        for item in items:
            hash = self._terminal_hash_manager.get_item_hash(item, self._data_type, account_type, region)
            data_dictionary[item[self._id_key]] = item
            hash_dictionary[item[self._id_key]] = hash

        dictionary_hash = self._get_array_xor(list(hash_dictionary.values()))
        self._update_category_record(category_name, dictionary_hash)
        self.remove_reference(connection_id, instance_index)

        if self._data_by_hash.get(dictionary_hash):
            self.add_reference(dictionary_hash, connection_id, instance_index)
        else:
            self._data_by_hash[dictionary_hash] = {
                "hashes": hash_dictionary,
                "data": data_dictionary,
                "removedItemIds": [],
                "parentHash": None,
                "childHashes": [],
                "lastUpdated": datetime.now().timestamp(),
                "references": {connection_id: [instance_index]},
            }

        return dictionary_hash

    def update_items(
        self,
        category_name: str,
        account_type: str,
        connection_id: str,
        instance_index: str,
        items: List,
        removed_item_ids: List[str],
        parent_hash: str,
    ) -> str:
        """Updates data ana returns new hash.

        Args:
            category_name: Category name.
            account_type: Account type.
            connection_id: Connection id.
            instance_index: Instance index.
            items: Items array.
            removed_item_ids: Removed item ids.
            parent_hash: Parent hash.

        Returns:
            updated dictionary hash.
        """
        if not parent_hash:
            return self.record_items(category_name, account_type, connection_id, instance_index, items)

        region = instance_index.split(":")[0]
        hash_dictionary = {}
        data_dictionary = {}
        parent_data = self.get_hashes_by_hash(parent_hash)

        if not parent_data:
            raise Exception("Parent data doesn't exist")
        else:
            parent_hash_dictionary = parent_data.copy()

            for item in items:
                hash = self._terminal_hash_manager.get_item_hash(item, self._data_type, account_type, region)
                data_dictionary[item[self._id_key]] = item
                hash_dictionary[item[self._id_key]] = hash
                parent_hash_dictionary[item[self._id_key]] = hash

            for removed_id in removed_item_ids:
                if removed_id in parent_hash_dictionary:
                    del parent_hash_dictionary[removed_id]

            dictionary_hash = self._get_array_xor(list(parent_hash_dictionary.values()))
            self._update_category_record(category_name, dictionary_hash)
            if dictionary_hash != parent_hash:
                self.remove_reference(connection_id, instance_index)
                if self._data_by_hash.get(dictionary_hash):
                    self.add_reference(dictionary_hash, connection_id, instance_index)
                elif dictionary_hash:
                    self._data_by_hash[dictionary_hash] = {
                        "hashes": hash_dictionary,
                        "data": data_dictionary,
                        "parentHash": parent_hash,
                        "removedItemIds": removed_item_ids,
                        "childHashes": [],
                        "lastUpdated": datetime.now().timestamp(),
                        "references": {connection_id: [instance_index]},
                    }
                    self._data_by_hash[parent_hash]['childHashes'].append(dictionary_hash)
            else:
                self.remove_reference(connection_id, instance_index)
                self.add_reference(dictionary_hash, connection_id, instance_index)

            return dictionary_hash

    def get_last_used_hashes(self, category_name: str) -> List[str]:
        """Returns the list of last used records hashes.

        Args:
            category_name: Category name.

        Returns:
            Last used records hashes.
        """
        search_hashes = []

        def get_top_hashes(category: str, hash_amount: int = None):
            category_data = self._hashes_by_category.get(category)
            if not category_data:
                return []
            else:
                hashes_array = []
                keys = sorted(list(category_data.keys()), reverse=True)

                for i in range(min(hash_amount, len(keys)) if hash_amount is not None else len(keys)):
                    hashes_array += category_data[keys[i]]

                return hashes_array

        if self._use_fuzzy_search:
            results = self._get_similar_category_names(category_name)
            # include all results from exact match
            if results and results[0] == category_name:
                search_hashes = get_top_hashes(category_name)
                results = results[1:]

            # include 3 latest updated hashes from close matches
            for category in results:
                search_hashes = search_hashes + get_top_hashes(category, 3)

        else:
            search_hashes = get_top_hashes(category_name, 20)

        return search_hashes[:20]

    def add_reference(self, hash: str, connection_id: str, instance_index: str):
        """Adds a reference from a terminal state instance index to a records hash.

        Args:
            hash: Records hash.
            connection_id: Connection id.
            instance_index: Instance index
        """
        if not self._data_by_hash.get(hash):
            raise Exception(f"Can't add reference - {self._data_type} data for hash {hash} doesn't exist")

        references = self._data_by_hash[hash]['references']

        if not references.get(connection_id):
            references[connection_id] = [instance_index]
        else:
            if instance_index not in references[connection_id]:
                references[connection_id].append(instance_index)

        self._data_by_hash[hash]['lastUpdated'] = datetime.now().timestamp()

    def remove_reference(self, connection_id: str, instance_index: str):
        for hash in self._data_by_hash:
            references = self._data_by_hash[hash]['references']
            if references.get(connection_id):
                with suppress(ValueError):
                    index = references[connection_id].index(instance_index)
                    references[connection_id].pop(index)

                if not references[connection_id]:
                    del references[connection_id]

    def _get_similar_category_names(self, category_name: str) -> List[str]:
        category_name_list = []
        for category in self._hashes_by_category:
            if rapidfuzz.fuzz.ratio(category_name, category) >= 70:
                category_name_list.append(category)
        return category_name_list

    def _get_array_xor(self, hex_array: List[str]) -> str:
        """Calculates hash from array of hashes.

        Args:
            hex_array: Array of hashes.

        Returns:
            Resulting hash.
        """
        return functools.reduce(lambda a, b: self._get_hex_xor(a, b), hex_array) if len(hex_array) > 0 else None

    def _get_hex_xor(self, hex1: str, hex2: str) -> str:
        buf1 = bytearray.fromhex(hex1)
        buf2 = bytearray.fromhex(hex2)
        buf_result = []

        for i in range(len(buf1)):
            buf_result.append(buf1[i] ^ buf2[i])

        return bytearray(buf_result).hex()

    def _update_category_record(self, category_name: str, hash: str):
        if not hash:
            return
        now = datetime.now().timestamp()
        self._remove_category_record(category_name, hash)
        if not self._hashes_by_category.get(category_name):
            self._hashes_by_category[category_name] = {}

        if not self._hashes_by_category[category_name].get(now):
            self._hashes_by_category[category_name][now] = []

        self._hashes_by_category[category_name][now].append(hash)

    def _remove_category_record(self, category_name: str, hash: str):
        if self._hashes_by_category.get(category_name):
            for timestamp in self._hashes_by_category[category_name].copy():
                if hash in self._hashes_by_category[category_name][timestamp].copy():
                    self._hashes_by_category[category_name][timestamp] = list(
                        filter(lambda item: item != hash, self._hashes_by_category[category_name][timestamp])
                    )
                    if not self._hashes_by_category[category_name][timestamp]:
                        del self._hashes_by_category[category_name][timestamp]

            if not self._hashes_by_category[category_name]:
                del self._hashes_by_category[category_name]

    def _optimize_trees_job(self):
        now = datetime.now().timestamp()

        for hash in self._data_by_hash.copy():
            data = self._data_by_hash[hash]
            if (
                data['lastUpdated'] <= now - self._record_expiration_time
                and not data['references']
                and len(data['childHashes']) < 2
            ):
                if len(data['childHashes']) == 1:
                    child_hash = data['childHashes'][0]
                    child_data = self._data_by_hash[child_hash]

                    if data.get('parentHash'):
                        combined_changes = {**data['data'], **child_data['data']}
                        combined_hashes = {**data['hashes'], **child_data['hashes']}
                        child_data_ids = list(child_data['data'].keys())
                        combined_removed_ids = (
                            list(filter(lambda id: id not in child_data_ids, data['removedItemIds']))
                            + child_data['removedItemIds']
                        )
                        child_data['data'] = combined_changes
                        child_data['hashes'] = combined_hashes
                        child_data['removedItemIds'] = combined_removed_ids
                        child_data['parentHash'] = data['parentHash']
                        self._data_by_hash[data['parentHash']]['childHashes'].append(child_hash)
                    else:
                        child_items = self.get_items_by_hash(child_hash)
                        child_hashes = self.get_hashes_by_hash(child_hash)
                        child_data['data'] = child_items
                        child_data['hashes'] = child_hashes
                        child_data['removedItemIds'] = []
                        child_data['parentHash'] = None

                if data.get('parentHash'):
                    parent_data = self._data_by_hash.get(data['parentHash'])

                    if parent_data:
                        parent_data['childHashes'] = list(
                            filter(lambda item_hash: hash != item_hash, parent_data['childHashes'])
                        )

                del self._data_by_hash[hash]

                for category in self._hashes_by_category:
                    self._remove_category_record(category, hash)

    def stop(self):
        """Stops reference tree optimize job & clears interval."""
        self._interval.cancel()
