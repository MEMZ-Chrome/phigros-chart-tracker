"""Phigros 本地资源拆包工具包。"""

from .catalog import load_catalog
from .extractors import extract_asset
from .pipeline import (
    extract_catalog_resource,
    list_catalog_resources,
)

__all__ = [
    "load_catalog",
    "extract_asset",
    "extract_catalog_resource",
    "list_catalog_resources",
]
