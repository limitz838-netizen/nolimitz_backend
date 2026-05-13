import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from metaapi_cloud_sdk import MetaApi

logger = logging.getLogger(__name__)

class MetaApiService:
    """
    Robust MetaApi Service with better deployment waiting and retry logic
    """

    _api: Optional[MetaApi] = None

    def __new__(cls):
        if not hasattr(cls, '_instance'):
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._api_instance = None
        self._initialized = True

    async def initialize(self):
        if MetaApiService._api is not None:
            self._api_instance = MetaApiService._api
            return

        token = os.getenv("METAAPI_TOKEN")
        if not token:
            raise RuntimeError("METAAPI_TOKEN is missing")

        MetaApiService._api = MetaApi(token)
        self._api_instance = MetaApiService._api

    async def _ensure_initialized(self):
        if self._api_instance is None:
            await self.initialize()

    @property
    def api(self):
        if self._api_instance is None:
            raise RuntimeError("Call await service.initialize() first")
        return self._api_instance

    # ========================= ACCOUNT =========================
    async def create_mt5_account(self, login: str, password: str, server: str, name: str):
        await self._ensure_initialized()
        return await self.api.metatrader_account_api.create_account({
            "name": name,
            "type": "cloud-g2",
            "login": str(login).strip(),
            "password": str(password).strip(),
            "server": str(server).strip(),
            "platform": "mt5",
            "magic": 20260401,
        })

    async def get_account(self, account_id: str):
        await self._ensure_initialized()
        return await self.api.metatrader_account_api.get_account(account_id)

    async def deploy_account_and_wait(self, account, timeout_seconds: int = 300):
        """Improved deployment with longer timeout and better status checking"""
        await self._ensure_initialized()

        await account.deploy()

        # Wait for deployment
        try:
            if hasattr(account, 'wait_deployed'):
                await asyncio.wait_for(account.wait_deployed(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning(f"wait_deployed timed out for {account.id}")

        # Extra wait for broker connection
        for attempt in range(8):  # ~2 minutes max
            try:
                state = getattr(account, 'state', None)
                if state in ['DEPLOYED', 'CONNECTED']:
                    break
                
                await asyncio.sleep(15)
                await account.reload()  # Refresh state
            except Exception:
                await asyncio.sleep(10)

        # Final connection wait
        try:
            await asyncio.wait_for(account.wait_connected(), timeout=120)
        except asyncio.TimeoutError:
            raise RuntimeError("Account failed to connect to broker after deployment")

        return account

    # ========================= CONNECTION =========================
    async def get_rpc_connection(self, account_id: str, max_retries: int = 3):
        await self._ensure_initialized()

        for attempt in range(max_retries):
            try:
                account = await self.get_account(account_id)
                
                # Ensure account is deployed
                state = getattr(account, 'state', None)

                if state not in [
                    "DEPLOYED",
                    "DEPLOYING",
                    "CONNECTED"
                ]:
                    await self.deploy_account_and_wait(account)

                connection = account.get_rpc_connection()

                await asyncio.wait_for(connection.connect(), timeout=60)
                await asyncio.wait_for(connection.wait_synchronized(), timeout=180)

                return account, connection

            except Exception as e:
                if attempt == max_retries - 1:
                    raise RuntimeError(f"Failed to get RPC connection after {max_retries} attempts: {e}")
                await asyncio.sleep(10 * (attempt + 1))

        raise RuntimeError("Could not establish RPC connection")

    # ========================= TRADING =========================
    async def get_positions(self, account_id: str) -> List[Dict]:
        _, connection = await self.get_rpc_connection(account_id)
        return await connection.get_positions()

    async def get_symbols(self, account_id: str) -> List[str]:
        _, connection = await self.get_rpc_connection(account_id)
        return await connection.get_symbols()

    async def create_market_buy_order(self, account_id: str, symbol: str, volume: float,
                                      stop_loss=None, take_profit=None, comment=""):
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

    async def create_market_sell_order(self, account_id: str, symbol: str, volume: float,
                                       stop_loss=None, take_profit=None, comment=""):
        _, connection = await self.get_rpc_connection(account_id)
        request = {
            "symbol": symbol,
            "volume": float(volume),
            "actionType": "ORDER_TYPE_SELL",
            "comment": str(comment)[:50],
        }
        if stop_loss: request["stopLoss"] = float(stop_loss)
        if take_profit: request["takeProfit"] = float(take_profit)

        return await connection.create_market_sell_order(request)

    async def close_position(self, account_id: str, position_id: str):
        _, connection = await self.get_rpc_connection(account_id)
        return await connection.close_position(str(position_id))

    async def get_account_info(self, account_id: str) -> Dict[str, Any]:
        account, connection = await self.get_rpc_connection(account_id)
        info = await connection.get_account_information()
        return {"account": account, "info": info}

    async def find_broker_symbol(self, account_id: str, symbol: str) -> str:
        symbols = await self.get_symbols(account_id)
        upper = str(symbol).strip().upper()
        for s in symbols:
            if upper in str(s).upper() or str(s).upper() in upper:
                return str(s)
        return symbol