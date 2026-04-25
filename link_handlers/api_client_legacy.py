"""
Legacy sync wrappers for link conversion.

These wrappers are offline-only and must not be used from the online async
message handling chain.
"""
from typing import Dict, Any, Optional

from .api_client import LinkConversionAPI
from utils.async_legacy import run_async_legacy_only


class LegacyLinkConversionAPI(LinkConversionAPI):
    def legacy_step1_parse_link(self, link: str) -> Optional[Dict[str, Any]]:
        return run_async_legacy_only(self.astep1_parse_link(link))

    def legacy_step2_convert_link(self, appid: str, page: str, p_value: str) -> Optional[Dict[str, Any]]:
        return run_async_legacy_only(self.astep2_convert_link(appid, page, p_value))

    def legacy_generate_custom_link(self, appid: str, path: str) -> Optional[str]:
        return run_async_legacy_only(self.agenerate_custom_link(appid, path))

    def legacy_convert_link(self, original_link: str, p_value: str) -> Optional[str]:
        return run_async_legacy_only(self.aconvert_link(original_link, p_value))

    def step1_parse_link(self, link: str) -> Optional[Dict[str, Any]]:
        return self.legacy_step1_parse_link(link)

    def step2_convert_link(self, appid: str, page: str, p_value: str) -> Optional[Dict[str, Any]]:
        return self.legacy_step2_convert_link(appid, page, p_value)

    def generate_custom_link(self, appid: str, path: str) -> Optional[str]:
        return self.legacy_generate_custom_link(appid, path)

    def convert_link(self, original_link: str, p_value: str) -> Optional[str]:
        return self.legacy_convert_link(original_link, p_value)
