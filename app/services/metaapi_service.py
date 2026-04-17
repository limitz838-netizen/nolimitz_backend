import asyncio
import os
from typing import Any, Dict, List, Optional

from metaapi_cloud_sdk import MetaApi


class MetaApiService:
    def __init__(self):
        token = os.getenv("METAAPI_TOKEN")
        if not token:
            raise RuntimeError("METAAPI_TOKEN is missing")

        self.api = MetaApi(token)

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
        return await self.api.metatrader_account_api.get_account(account_id)

    async def deploy_account_and_wait(
        self,
        account,
        connect_timeout_seconds: int = 180,
    ):
        await account.deploy()
        await asyncio.wait_for(
            account.wait_connected(),
            timeout=connect_timeout_seconds,
        )
        return account

    async def undeploy_account(self, account_id: str):
        account = await self.get_account(account_id)
        await account.undeploy()
        return True

    async def get_rpc_connection(self, account_id: str):
        account = await self.get_account(account_id)
        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized()
        return account, connection

    async def get_account_info(self, account_id: str) -> Dict[str, Any]:
        account, connection = await self.get_rpc_connection(account_id)
        info = await connection.get_account_information()
        return {
            "account": account,
            "info": info,
        }

    async def get_positions(self, account_id: str) -> List[Dict[str, Any]]:
        _, connection = await self.get_rpc_connection(account_id)
        return await connection.get_positions()

    async def get_position(self, account_id: str, position_id: str) -> Optional[Dict[str, Any]]:
        _, connection = await self.get_rpc_connection(account_id)
        try:
            return await connection.get_position(position_id=str(position_id))
        except Exception:
            return None

    async def get_symbols(self, account_id: str) -> List[str]:
        _, connection = await self.get_rpc_connection(account_id)
        return await connection.get_symbols()

    async def get_symbol_specification(self, account_id: str, symbol: str) -> Optional[Dict[str, Any]]:
        _, connection = await self.get_rpc_connection(account_id)
        try:
            return await connection.get_symbol_specification(symbol=symbol)
        except Exception:
            return None

    async def get_symbol_price(self, account_id: str, symbol: str) -> Optional[Dict[str, Any]]:
        _, connection = await self.get_rpc_connection(account_id)
        try:
            return await connection.get_symbol_price(symbol=symbol)
        except Exception:
            return None

    async def find_broker_symbol(self, account_id: str, requested_symbol: str) -> str:
        requested = requested_symbol.upper().strip()
        symbols = await self.get_symbols(account_id)

        if not symbols:
            raise Exception("No broker symbols returned from MetaApi")

        for name in symbols:
            if str(name).upper() == requested:
                return str(name)

        for name in symbols:
            upper_name = str(name).upper()
            if upper_name.startswith(requested):
                return str(name)
            if upper_name.endswith(requested):
                return str(name)
            if requested in upper_name:
                return str(name)

        if requested in ["XAUUSD", "XAUUSDM"]:
            gold_candidates = []
            for name in symbols:
                upper_name = str(name).upper()
                if "XAUUSD" in upper_name or "GOLD" in upper_name:
                    gold_candidates.append(str(name))
            if gold_candidates:
                return gold_candidates[0]

        raise Exception(f"Symbol not found on broker for requested symbol: {requested_symbol}")

    async def create_market_buy_order(
        self,
        account_id: str,
        symbol: str,
        volume: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        comment: str = "Nolimitz Copier",
        client_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        _, connection = await self.get_rpc_connection(account_id)

        options = {"comment": comment}
        if client_id:
            options["clientId"] = client_id

        return await connection.create_market_buy_order(
            symbol=symbol,
            volume=volume,
            stop_loss=stop_loss,
            take_profit=take_profit,
            options=options,
        )

    async def create_market_sell_order(
        self,
        account_id: str,
        symbol: str,
        volume: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        comment: str = "Nolimitz Copier",
        client_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        _, connection = await self.get_rpc_connection(account_id)

        options = {"comment": comment}
        if client_id:
            options["clientId"] = client_id

        return await connection.create_market_sell_order(
            symbol=symbol,
            volume=volume,
            stop_loss=stop_loss,
            take_profit=take_profit,
            options=options,
        )

    async def modify_position(
        self,
        account_id: str,
        position_id: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Dict[str, Any]:
        _, connection = await self.get_rpc_connection(account_id)
        return await connection.modify_position(
            position_id=str(position_id),
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    async def close_position(
        self,
        account_id: str,
        position_id: str,
    ) -> Dict[str, Any]:
        _, connection = await self.get_rpc_connection(account_id)
        return await connection.close_position(position_id=str(position_id))