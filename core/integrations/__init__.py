"""
Third-party API integrations (Figma, etc.).
"""

from core.integrations.figma import FigmaClient, get_figma_client

__all__ = ["FigmaClient", "get_figma_client"]
