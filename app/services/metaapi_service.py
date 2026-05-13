import asyncio
import os
from typing import Any, Dict, List, Optional

from metaapi_cloud_sdk import MetaApi


class MetaApiService:
    """
    MetaApi Service with lazy initialization to avoid event loop issues in FastAPI.
    """

    _api: Optional[MetaApi] = None
    _initialized = False

    def __init__(self):
        self._api_instance = None

    async def initialize(self):
        """Initialize the MetaApi instance asynchronously"""
        if MetaApiService._api is not None:
            self._api_instance = MetaApiService._api
            return

        token = os.getenv("METAAPI_TOKEN")
        if not token:
            raise RuntimeError("METAAPI_TOKEN environment variable is missing")

        # Create the MetaApi instance in async context
        MetaApiService._api = MetaApi(token)
        self._api_instance = MetaApiService._api
        MetaApiService._initialized = True

    @property
    def api(self):
        if self._api_instance is None:
            raise RuntimeError("MetaApiService not initialized. Call await service.initialize() first.")
        return self._api_instance

    # =========================
    # ACCOUNT MANAGEMENT
    # =========================
    async def create_mt5_account(
        self, login: str, password: str, server: str, name: str, platform: str = "mt5"
    ):
        await self._ensure_initialized()
        account = await self.api.metatrader_account_api.create_account({
            "name": name,
            "type": "cloud-g2",
            "login": login.strip(),
            "password": password.strip(),
            "server": server.strip(),
            "platform": platform,
            "magic": 20260401,
        })
        return account

    async def get_account(self, account_id: str):
        await self._ensure_initialized()
        return await self.api.metatrader_account_api.get_account(account_id)

    async def _ensure_initialized(self):
        if self._api_instance is None:
            await self.initialize()

    # ... (rest of your methods)

    async def deploy_account_and_wait(self, account, timeout_seconds: int = 180):
        await self._ensure_initialized()
        await account.deploy()

        try:
            if hasattr(account, "wait_deployed"):
                await asyncio.wait_for(account.wait_deployed(), timeout=timeout_seconds)
            await asyncio.wait_for(account.wait_connected(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            raise RuntimeError(f"Deployment timed out after {timeout_seconds}s")
        return account

    async def get_rpc_connection(self, account_id: str):
        await self._ensure_initialized()
        account = await self.get_account(account_id)

        if getattr(account, "state", None) not in ["DEPLOYED", "CONNECTED"]:
            await self.deploy_account_and_wait(account)

        connection = account.get_rpc_connection()

        try:
            await asyncio.wait_for(connection.connect(), timeout=30)
            await asyncio.wait_for(connection.wait_synchronized(), timeout=120)
        except asyncio.TimeoutError:
            raise RuntimeError("Failed to connect to MetaTrader (timeout)")
        except Exception as e:
            raise RuntimeError(f"Connection failed: {str(e)}") from e

        return account, connection

    async def get_account_info(self, account_id: str) -> Dict[str, Any]:
        account, connection = await self.get_rpc_connection(account_id)
        info = await connection.get_account_information()
        return {"account": account, "info": info}

    # Add the rest of your methods (get_positions, get_symbols, etc.) similarly...
    async def get_positions(self, account_id: str):
        await self._ensure_initialized()
        _, connection = await self.get_rpc_connection(account_id)
        return await connection.get_positions()

    async def get_symbol_price(self, account_id: str, symbol: str):
        await self._ensure_initialized()
        _, connection = await self.get_rpc_connection(account_id)
        try:
            return await connection.get_symbol_price(symbol=symbol)
        except Exception:
            return None