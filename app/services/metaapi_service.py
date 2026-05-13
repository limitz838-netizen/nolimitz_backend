import asyncio
import os
from typing import Any, Dict, List, Optional

from metaapi_cloud_sdk import MetaApi


class MetaApiService:
    """
    MetaApi Cloud SDK Service - Optimized for FastAPI + Background Worker
    """

    _api: Optional[MetaApi] = None
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return

        self._api_instance = None
        self._initialized = True

    async def initialize(self):
        """Initialize MetaApi (must be called in async context)"""
        if MetaApiService._api is not None:
            self._api_instance = MetaApiService._api
            return

        token = os.getenv("METAAPI_TOKEN")
        if not token:
            raise RuntimeError("METAAPI_TOKEN environment variable is missing!")

        MetaApiService._api = MetaApi(token)
        self._api_instance = MetaApiService._api

    async def _ensure_initialized(self):
        if self._api_instance is None:
            await self.initialize()

    @property
    def api(self):
        if self._api_instance is None:
            raise RuntimeError("MetaApiService not initialized. Await service.initialize() first.")
        return self._api_instance

    # ========================= ACCOUNT MANAGEMENT =========================
    async def create_mt5_account(
        self,
        login: str,
        password: str,
        server: str,
        name: str,
        platform: str = "mt5",
    ):
        await self._ensure_initialized()
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
        await self._ensure_initialized()
        return await self.api.metatrader_account_api.get_account(account_id)

    async def deploy_account_and_wait(self, account, timeout_seconds: int = 180):
        await self._ensure_initialized()
        await account.deploy()

        try:
            if hasattr(account, "wait_deployed"):
                await asyncio.wait_for(account.wait_deployed(), timeout=timeout_seconds)
            await asyncio.wait_for(account.wait_connected(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            raise RuntimeError(f"Account deployment timed out after {timeout_seconds} seconds")
        return account

    # ========================= CONNECTION =========================
    async def get_rpc_connection(self, account_id: str):
        await self._ensure_initialized()
        account = await self.get_account(account_id)

        # Ensure account is deployed
        if getattr(account, 'state', None) not in [
            "DEPLOYED",
            "DEPLOYING",
            "CONNECTED"
        ]:
            await self.deploy_account_and_wait(account)

        connection = account.get_rpc_connection()

        try:
            await asyncio.wait_for(connection.connect(), timeout=40)

        except asyncio.TimeoutError:
            raise RuntimeError("MetaApi connection timeout")

        except Exception:
            await asyncio.sleep(2)

            try:
                await asyncio.wait_for(connection.connect(), timeout=40)

            except asyncio.TimeoutError:
                raise RuntimeError("MetaApi retry connection timeout")

            except Exception as e:
                raise RuntimeError(f"RPC connection failed: {e}") from e

        await asyncio.wait_for(
            connection.wait_synchronized(),
            timeout=120
        )

        return account, connection

    async def get_account_info(self, account_id: str) -> Dict[str, Any]:
        account, connection = await self.get_rpc_connection(account_id)
        info = await connection.get_account_information()
        return {"account": account, "info": info}

    # ========================= TRADING METHODS (Used by Worker) =========================
    async def get_positions(self, account_id: str) -> List[Dict]:
        await self._ensure_initialized()
        _, connection = await self.get_rpc_connection(account_id)
        return await connection.get_positions()

    async def get_symbols(self, account_id: str) -> List[str]:
        await self._ensure_initialized()
        _, connection = await self.get_rpc_connection(account_id)
        return await connection.get_symbols()

    async def create_market_buy_order(
        self,
        account_id: str,
        symbol: str,
        volume: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        comment: str = "",
    ):
        await self._ensure_initialized()
        _, connection = await self.get_rpc_connection(account_id)

        request = {
            "symbol": symbol,
            "volume": float(volume),
            "actionType": "ORDER_TYPE_BUY",
            "comment": str(comment)[:50],
        }
        if stop_loss is not None:
            request["stopLoss"] = float(stop_loss)
        if take_profit is not None:
            request["takeProfit"] = float(take_profit)

        return await connection.create_market_buy_order(request)

    async def create_market_sell_order(
        self,
        account_id: str,
        symbol: str,
        volume: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        comment: str = "",
    ):
        await self._ensure_initialized()
        _, connection = await self.get_rpc_connection(account_id)

        request = {
            "symbol": symbol,
            "volume": float(volume),
            "actionType": "ORDER_TYPE_SELL",
            "comment": str(comment)[:50],
        }
        if stop_loss is not None:
            request["stopLoss"] = float(stop_loss)
        if take_profit is not None:
            request["takeProfit"] = float(take_profit)

        return await connection.create_market_sell_order(request)

    async def close_position(self, account_id: str, position_id: str):
        await self._ensure_initialized()
        _, connection = await self.get_rpc_connection(account_id)
        return await connection.close_position(str(position_id))

    # ========================= UTILITY =========================
    async def find_broker_symbol(self, account_id: str, symbol: str) -> str:
        """Fallback method to find closest matching symbol"""
        await self._ensure_initialized()
        symbols = await self.get_symbols(account_id)
        upper_symbol = normalize_symbol(symbol)  # You can import or define normalize_symbol here

        for s in symbols:
            if normalize_symbol(s) == upper_symbol or upper_symbol in normalize_symbol(s):
                return str(s)
        return symbol  # return original as last resort


def normalize_symbol(value: str) -> str:
    """Helper function inside service"""
    return str(value).strip().upper() if value else ""