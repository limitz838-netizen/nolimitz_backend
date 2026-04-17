import base64
import json
import re
from typing import List, Union

from ..clients.metaapi.token_management_client import (
    AccessRuleResource,
    ManifestAccessRule,
    NarrowDownAccessRules,
    NarrowDownSimplifiedAccessRules,
    TokenManagementClient,
)


class TokenManagementApi:
    """Exposes TokenManagement API logic to the consumers."""

    def __init__(self, token_management_client: TokenManagementClient):
        """Initializes a TokenManagement API instance.

        Args:
            token_management_client: TokenManagement REST API client.
        """
        self._token_management_client = token_management_client

    async def get_access_rules(self) -> List[ManifestAccessRule]:
        """Gets access rules manifest.

        Returns:
            A coroutine resolving with access rules manifest.
        """
        return await self._token_management_client.get_access_rules()

    async def narrow_down_token(
        self,
        narrow_down_payload: Union[NarrowDownAccessRules, NarrowDownSimplifiedAccessRules],
        validity_in_hours: float = None,
    ) -> str:
        """Returns narrowed down token with given access rules.

        Args:
            narrow_down_payload: Narrow down payload.
            validity_in_hours: Token validity in hours, default is 24 hours.

        Returns:
            A coroutine resolving with narrowed down token.
        """
        narrowed_token = await self._token_management_client.narrow_down_token(narrow_down_payload, validity_in_hours)
        return narrowed_token['token']

    async def narrow_down_token_resources(
        self, resources: List[AccessRuleResource], validity_in_hours: float = None
    ) -> str:
        """Returns narrowed down token with access to given resources.

        Args:
            resources: Resources to grant access to.
            validity_in_hours: Token validity in hours, default is 24 hours.

        Returns:
            A coroutine resolving with narrowed down token.
        """
        narrowed_token = await self._token_management_client.narrow_down_token(
            {'resources': resources}, validity_in_hours
        )
        return narrowed_token['token']

    async def narrow_down_token_roles(self, roles: List[str], validity_in_hours: float = None) -> str:
        """Returns narrowed down token with access to given roles.

        Args:
            roles: Roles to grant access to.
            validity_in_hours: Token validity in hours, default is 24 hours.

        Returns:
            A coroutine resolving with narrowed down token.
        """
        narrowed_token = await self._token_management_client.narrow_down_token({'roles': roles}, validity_in_hours)
        return narrowed_token['token']

    async def narrow_down_token_applications(self, applications: List[str], validity_in_hours: float = None) -> str:
        """Returns narrowed down token with access to given applications.

        Args:
            applications: Applications to grant access to.
            validity_in_hours: Token validity in hours, default is 24 hours.

        Returns:
            A coroutine resolving with narrowed down token.
        """
        narrowed_token = await self._token_management_client.narrow_down_token(
            {'applications': applications}, validity_in_hours
        )
        return narrowed_token['token']

    def are_token_resources_narrowed_down(self, token: str) -> bool:
        """Checks if token resources access is restricted.

        Args:
            token: Token to check.

        Returns:
            Is token narrowed down.
        """
        parsed_payload = json.loads(base64.b64decode(token.split(".")[1] + "=="))
        are_resources_restricted = any(
            rule
            for rule in parsed_payload["accessRules"]
            if any(not re.match(r"^\*:\S*:\*$", resource) for resource in rule["resources"])
        )
        if are_resources_restricted:
            return True
        return False
