from copy import deepcopy
from typing import List, TypedDict

from .provisioning_profile import ProvisioningProfile
from ..clients.metaapi.provisioning_profile_client import (
    ProvisioningProfileClient,
    NewProvisioningProfileDto,
    ProvisioningProfilesFilter,
)


class ProvisioningProfilesList(TypedDict):
    """Provisioning profile list entity."""

    count: int
    """Provisioning profiles count."""
    items: List[ProvisioningProfile]
    """Provisioning profile entities list."""


class ProvisioningProfileApi:
    """Exposes provisioning profile API logic to the consumers."""

    def __init__(self, provisioning_profile_client: ProvisioningProfileClient):
        """Initializes a provisioning profile API instance.

        Args:
            provisioning_profile_client: Provisioning profile REST API client.
        """
        self._provisioning_profile_client = provisioning_profile_client

    async def get_provisioning_profiles_with_infinite_scroll_pagination(
        self, filter: ProvisioningProfilesFilter = None
    ) -> List[ProvisioningProfile]:
        """Retrieves provisioning profiles, provides pagination in infinite scroll style

        Args:
            filter: Provisioning profiles filter.

        Returns:
            A coroutine resolving with an array of provisioning profile entities.
        """

        profiles = await self._provisioning_profile_client.get_provisioning_profiles(filter, '1')
        return list(map(lambda profile: ProvisioningProfile(profile, self._provisioning_profile_client), profiles))

    async def get_provisioning_profiles_with_classic_pagination(
        self, filter: ProvisioningProfilesFilter = None
    ) -> List[ProvisioningProfilesList]:
        """Retrieves provisioning profiles and count, provides pagination in a classic style.

        Args:
            filter: Provisioning profiles filter.

        Returns:
            A coroutine resolving with an array of provisioning profile entities and count.
        """
        profiles = await self._provisioning_profile_client.get_provisioning_profiles(filter, '2')
        return {
            'count': len(profiles),
            'items': list(
                map(lambda profile: ProvisioningProfile(profile, self._provisioning_profile_client), profiles)
            ),
        }

    async def get_provisioning_profile(self, provisioning_profile_id: str) -> ProvisioningProfile:
        """Retrieves a provisioning profile by id.

        Args:
            provisioning_profile_id: Provisioning profile id.

        Returns:
            A coroutine resolving with provisioning profile entity.
        """
        profile = await self._provisioning_profile_client.get_provisioning_profile(provisioning_profile_id)
        return ProvisioningProfile(profile, self._provisioning_profile_client)

    async def create_provisioning_profile(self, profile: NewProvisioningProfileDto) -> ProvisioningProfile:
        """Creates a provisioning profile.

        Args:
            profile: Provisioning profile data.

        Returns:
            A coroutine resolving with provisioning profile entity.
        """

        id = await self._provisioning_profile_client.create_provisioning_profile(profile)
        new_profile = deepcopy(profile)
        new_profile['_id'] = id['id']
        new_profile['status'] = 'new'
        return ProvisioningProfile(new_profile, self._provisioning_profile_client)
