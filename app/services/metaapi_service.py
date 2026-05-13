import asyncio
import os
from typing import Any, Dict, List, Optional

from metaapi_cloud_sdk import MetaApi


class MetaApiService:

    _api = None

    def __init__(self):

        token = os.getenv("METAAPI_TOKEN")

        if not token:
            raise Exception("METAAPI_TOKEN missing")

        # Create event loop if missing
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Singleton MetaApi instance
        if MetaApiService._api is None:
            MetaApiService._api = MetaApi(token)

        self.api = MetaApiService._api

    async def create_mt5_account(
        self,
        login: str,
        password: str,
        server: str,
        name: str,
        platform: str = "mt5",
    ):

        account = await self.api.metatrader_account_api.create_account({
            "name": name,
            "type": "cloud-g2",
            "login": str(login).strip(),
            "password": str(password).strip(),
            "server": str(server).strip(),
            "platform": platform,
            "magic": 20260401,
        })

        return account

    async def get_account(self, account_id: str):

        return await self.api.metatrader_account_api.get_account(
            account_id
        )

    async def deploy_account_and_wait(
        self,
        account,
        connect_timeout_seconds: int = 180,
    ):

        await account.deploy()

        if hasattr(account, "wait_deployed"):
            await asyncio.wait_for(
                account.wait_deployed(),
                timeout=connect_timeout_seconds
            )

        await asyncio.wait_for(
            account.wait_connected(),
            timeout=connect_timeout_seconds
        )

        return account

    async def undeploy_account(self, account_id: str):

        account = await self.get_account(account_id)

        await account.undeploy()

        return True

    async def get_rpc_connection(self, account_id: str):

        account = await self.get_account(account_id)

        state = getattr(account, "state", None)

        if state not in ["DEPLOYED", "DEPLOYING", "CONNECTED"]:
            await self.deploy_account_and_wait(account)

        if hasattr(account, "wait_deployed"):
            try:
                await asyncio.wait_for(
                    account.wait_deployed(),
                    timeout=180
                )
            except Exception:
                pass

        try:
            await asyncio.wait_for(
                account.wait_connected(),
                timeout=180
            )
        except Exception:
            pass

        connection = account.get_rpc_connection()

        try:
            await connection.connect()
        except Exception:
            await asyncio.sleep(2)
            await connection.connect()

        await asyncio.wait_for(
            connection.wait_synchronized(),
            timeout=120
        )

        return account, connection

    async def get_account_info(
        self,
        account_id: str
    ) -> Dict[str, Any]:

        account, connection = await self.get_rpc_connection(
            account_id
        )

        info = await connection.get_account_information()

        return {
            "account": account,
            "info": info,
        }

    async def get_positions(
        self,
        account_id: str
    ) -> List[Dict[str, Any]]:

        _, connection = await self.get_rpc_connection(account_id)

        return await connection.get_positions()

    async def get_position(
        self,
        account_id: str,
        position_id: str
    ) -> Optional[Dict[str, Any]]:

        _, connection = await self.get_rpc_connection(account_id)

        try:
            return await connection.get_position(
                position_id=str(position_id)
            )
        except Exception:
            return None

    async def get_symbols(
        self,
        account_id: str
    ) -> List[str]:

        _, connection = await self.get_rpc_connection(account_id)

        return await connection.get_symbols()

    async def get_symbol_specification(
        self,
        account_id: str,
        symbol: str
    ) -> Optional[Dict[str, Any]]:

        _, connection = await self.get_rpc_connection(account_id)

        try:
            return await connection.get_symbol_specification(
                symbol=symbol
            )
        except Exception:
            return None

    async def get_symbol_price(
        self,
        account_id: str,
        symbol: str
    ) -> Optional[Dict[str, Any]]:

        _, connection = await self.get_rpc_connection(account_id)

        try:
            return await connection.get_symbol_price(
                symbol=symbol
            )
        except Exception:
            return None