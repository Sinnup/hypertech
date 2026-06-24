"""
Figma integration — dual interface: MCP server (interactive) + REST API (autonomous).

**Interactive (Claude Code / IDE):**
  Uses the Figma MCP server at ``https://mcp.figma.com/mcp``.  Tools:
  ``get_design_context`` (structured React+Tailwind), ``get_screenshot``,
  ``get_variable_defs``, ``use_figma`` (Plugin API), ``search_design_system``.

  Required flow (per ``figma-use`` skill)::

      1. get_design_context  → structured representation for the exact node(s)
      2. get_screenshot       → visual reference of the node variant
      3. Download assets      → via assets endpoint
      4. Implement            → translate into project conventions

  **Skill reference:** ``~/.claude/skills/figma/`` — ``figma-use``, ``figma-generate-design``,
  ``figma-code-connect``, ``figma-generate-library``, ``figma-swiftui``.
  The ``figma-use`` skill is MANDATORY before every ``use_figma`` tool call.

**Autonomous (pipeline):**
  Uses the Figma REST API (``https://api.figma.com/v1``) with
  ``FIGMA_API_KEY`` + ``FIGMA_PROJECT_ID`` from ``.env``.  This class provides
  programmatic access for the UX/UI agent to fetch files, extract design tokens,
  and export screen assets without human interaction.

Credentials: ``FIGMA_API_KEY`` and ``FIGMA_PROJECT_ID`` in ``.env``.

Usage::

    from core.integrations.figma import FigmaClient
    client = FigmaClient()
    files = client.get_project_files()
    images = client.export_screen("file_key", "node_id")
    tokens = client.extract_tokens("file_key")
"""

import json
import time
import requests
from pathlib import Path
from typing import Optional

from core.secrets.loader import get_optional

BASE_URL = "https://api.figma.com/v1"

# Figma's rate limit is ~120 req/min for most endpoints,
# but document downloads are heavier.  Pause between calls
# when a 429 response is received.
_RATE_LIMIT_PAUSE = 3.0  # seconds


class FigmaClient:
    """Minimal Figma REST API wrapper for design asset extraction."""

    def __init__(self):
        self.api_key = get_optional("FIGMA_API_KEY", "")
        self.project_id = get_optional("FIGMA_PROJECT_ID", "")
        self._session = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.project_id)

    @property
    def session(self):
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"X-Figma-Token": self.api_key})
        return self._session

    # ------------------------------------------------------------------
    # Internal — rate-limit aware HTTP helpers
    # ------------------------------------------------------------------

    def _get(self, url: str, **kwargs) -> requests.Response:
        """
        GET with automatic 429 retry.  Pauses and retries up to 3 times
        when Figma rate-limits, respecting the ``Retry-After`` header if
        present.
        """
        max_retries = 3
        for attempt in range(max_retries):
            resp = self.session.get(url, **kwargs)
            if resp.status_code == 429 and attempt < max_retries - 1:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else _RATE_LIMIT_PAUSE * (attempt + 1)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        # Last attempt — let raise_for_status handle it
        resp = self.session.get(url, **kwargs)
        resp.raise_for_status()
        return resp

    # ------------------------------------------------------------------
    # Project & files
    # ------------------------------------------------------------------

    def get_project_files(self) -> list[dict]:
        """Return all Figma files in the configured project."""
        if not self.configured:
            return []
        url = f"{BASE_URL}/projects/{self.project_id}/files"
        resp = self._get(url, timeout=15)
        return resp.json().get("files", [])

    def get_file(self, file_key: str, depth: int = 2) -> dict:
        """Return the full document tree for a Figma file."""
        url = f"{BASE_URL}/files/{file_key}"
        params = {"depth": depth}
        resp = self._get(url, params=params, timeout=20)
        return resp.json()

    def get_file_nodes(self, file_key: str, node_ids: list[str]) -> dict:
        """Return specific nodes from a Figma file."""
        url = f"{BASE_URL}/files/{file_key}/nodes"
        params = {"ids": ",".join(node_ids)}
        resp = self._get(url, params=params, timeout=20)
        return resp.json()

    # ------------------------------------------------------------------
    # Asset export
    # ------------------------------------------------------------------

    def export_image(
        self,
        file_key: str,
        node_id: str,
        format: str = "png",
        scale: float = 2.0,
    ) -> Optional[bytes]:
        """Export a single node as an image.  Returns raw bytes."""
        url = f"{BASE_URL}/images/{file_key}"
        params = {"ids": node_id, "format": format, "scale": scale}
        resp = self._get(url, params=params, timeout=30)
        images = resp.json().get("images", {})
        image_url = images.get(node_id)
        if not image_url:
            return None
        # Image CDN URL — not rate-limited by Figma API
        img_resp = self.session.get(image_url, timeout=30)
        img_resp.raise_for_status()
        return img_resp.content

    def export_screens(
        self,
        file_key: str,
        node_ids: list[str],
        target_dir: Path,
        format: str = "png",
        scale: float = 2.0,
    ) -> list[Path]:
        """Export multiple nodes as images and save them to *target_dir*."""
        target_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for nid in node_ids:
            try:
                data = self.export_image(file_key, nid, format=format, scale=scale)
                if data:
                    safe_name = nid.replace(":", "_").replace("/", "_")
                    path = target_dir / f"{safe_name}.{format}"
                    path.write_bytes(data)
                    saved.append(path)
            except Exception as exc:
                print(f"  ⚠️  Figma export failed for {nid}: {exc}")
        return saved

    # ------------------------------------------------------------------
    # Design tokens
    # ------------------------------------------------------------------

    def extract_tokens(self, file_key: str, node_id: Optional[str] = None) -> dict:
        """
        Walk a Figma file and extract design tokens:
        colors, typography, spacing, border radii, shadows.

        Returns a dict suitable for merging into the UX/UI design brief.
        """
        if node_id:
            data = self.get_file_nodes(file_key, [node_id])
            doc = data.get("nodes", {}).get(node_id, {}).get("document", {})
        else:
            data = self.get_file(file_key, depth=3)
            doc = data.get("document", {})

        tokens = {"colors": {}, "typography": {}, "spacing": [], "radii": [], "shadows": []}
        self._walk_nodes(doc, tokens)
        return tokens

    def extract_tokens_from_document(self, document: dict) -> dict:
        """
        Extract design tokens from an already-loaded Figma document node
        (no API call).  Useful for testing and offline token extraction.
        """
        tokens = {"colors": {}, "typography": {}, "spacing": [], "radii": [], "shadows": []}
        self._walk_nodes(document, tokens)
        return tokens

    def _walk_nodes(self, node: dict, tokens: dict):
        """Recursively walk Figma nodes and collect design tokens."""
        if not isinstance(node, dict):
            return

        node_type = node.get("type", "")

        # Colors from fills
        fills = node.get("fills", [])
        for fill in fills:
            if fill.get("type") == "SOLID":
                color = fill.get("color", {})
                r = int(color.get("r", 0) * 255)
                g = int(color.get("g", 0) * 255)
                b = int(color.get("b", 0) * 255)
                a = color.get("a", 1.0)
                hex_color = f"#{r:02x}{g:02x}{b:02x}"
                if a < 1.0:
                    hex_color += f"{int(a*255):02x}"
                name = node.get("name", hex_color)
                if hex_color not in tokens["colors"]:
                    tokens["colors"][name] = hex_color

        # Typography
        style = node.get("style", {})
        if style.get("fontFamily"):
            tokens["typography"][node.get("name", "unknown")] = {
                "family": style.get("fontFamily"),
                "size": style.get("fontSize", "?"),
                "weight": style.get("fontWeight", "?"),
            }

        # Spacing
        if node_type == "FRAME" and node.get("layoutMode"):
            tokens["spacing"].append({
                "name": node.get("name", "frame"),
                "padding": node.get("paddingLeft", 0),
                "gap": node.get("itemSpacing", 0),
            })

        # Border radius
        if node.get("cornerRadius"):
            tokens["radii"].append({
                "name": node.get("name", "element"),
                "value": node["cornerRadius"],
            })

        # Shadows
        effects = node.get("effects", [])
        for effect in effects:
            if effect.get("type") == "DROP_SHADOW":
                tokens["shadows"].append({
                    "name": node.get("name", "element"),
                    "x": effect.get("offset", {}).get("x", 0),
                    "y": effect.get("offset", {}).get("y", 0),
                    "blur": effect.get("radius", 0),
                    "color": effect.get("color", {}),
                })

        # Recurse into children
        for child in node.get("children", []):
            self._walk_nodes(child, tokens)

    # ------------------------------------------------------------------
    # Component listing
    # ------------------------------------------------------------------

    def get_components(self, file_key: str) -> list[dict]:
        """Return all components defined in a Figma file."""
        url = f"{BASE_URL}/files/{file_key}/components"
        resp = self._get(url, timeout=15)
        resp.raise_for_status()
        return resp.json().get("meta", {}).get("components", [])


# ----------------------------------------------------------------------
# Convenience factory
# ----------------------------------------------------------------------

def get_figma_client() -> Optional[FigmaClient]:
    """Return a configured FigmaClient, or None if credentials are missing."""
    client = FigmaClient()
    return client if client.configured else None
