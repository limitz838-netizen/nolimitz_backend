import asyncio
import os
from typing import Any, Dict, List, Optional

from metaapi_cloud_sdk import MetaApi


class MetaApiService:
    """
    MetaApi Cloud SDK Service with proper FastAPI compatibility.
    """

    _api: Optional[MetaApi] = None
    _instance = None

    def __new__(cls):
        """Singleton pattern"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return

        token = os.getenv("METAAPI_TOKEN")
        if not token:
            raise RuntimeError("METAAPI_TOKEN environment variable is missing")

        self.api = self._get_or_create_api(token)
        self._initialized = True

    def _get_or_create_api(self, token: str) -> MetaApi:
        """Ensure only one MetaApi instance is created"""
        if MetaApiService._api is None:
            MetaApiService._api = MetaApi(token)
        return MetaApiService._api

    # =========================
    # ACCOUNT MANAGEMENT
    # =========================
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
            "login": login.strip(),
            "password": password.strip(),
            "server": server.strip(),
            "platform": platform,
            "magic": 20260401,
        })
        return account

    async def get_account(self, account_id: str):
        return await self.api.metatrader_account_api.get_account(account_id)

    async def deploy_account_and_wait(
        self,
        account,
        timeout_seconds: int = 180,
    ):
        await account.deploy()

        try:
            if hasattr(account, "wait_deployed"):
                await asyncio.wait_for(account.wait_deployed(), timeout=timeout_seconds)
            
            await asyncio.wait_for(account.wait_connected(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            raise RuntimeError(f"Account deployment timed out after {timeout_seconds} seconds")
        
        return account

    async def undeploy_account(self, account_id: str):
        account = await self.get_account(account_id)
        await account.undeploy()
        return True

    # =========================
    # CONNECTION
    # =========================
    async def get_rpc_connection(self, account_id: str):
        account = await self.get_account(account_id)

        # Ensure deployed
        if getattr(account, "state", None) not in ["DEPLOYED", "CONNECTED"]:
            await self.deploy_account_and_wait(account)

        connection = account.get_rpc_connection()

        try:
            await asyncio.wait_for(connection.connect(), timeout=30)
            await asyncio.wait_for(connection.wait_synchronized(), timeout=120)
        except asyncio.TimeoutError:
            raise RuntimeError("Failed to connect to MetaTrader account (timeout)")
        except Exception as e:
            raise RuntimeError(f"Connection error: {str(e)}") from e

        return account, connection

    # =========================
    # ACCOUNT INFO
    # =========================
    async def get_account_info(self, account_id: str) -> Dict[str, Any]:
        account, connection = await self.get_rpc_connection(account_id)
        info = await connection.get_account_information()

        return {
            "account": account,
            "info": info,
        }

    # =========================
    # POSITIONS & SYMBOLS
    # =========================
    async def get_positions(self, account_id: str) -> List[Dict[str, Any]]:
        _, connection = await self.get_rpc_connection(account_id)
        return await connection.get_positions()

    async def get_position(self, account_id: str, position_id: str) -> Optional[Dict[str, Any]]:
        _, connection = await self.get_rpc_connection(account_id)
        try:
            return await connection.get_position(str(position_id))
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