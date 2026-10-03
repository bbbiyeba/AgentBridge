"""Show frames from a Figma file on the site as live-rendered images.

Edit the design in Figma and the site picks up the new render within the
cache window -- no export/upload step. Figma's own rendered-image URLs
are returned as-is (they're public S3 links that stay valid for weeks),
so image bytes never pass through this server.
"""

from flask import current_app

from . import http
from .base import Integration, IntegrationError, Route, Setting, TTLCache, parse_list

API = "https://api.figma.com/v1"
MAX_FRAMES = 12


class FigmaIntegration(Integration):
    name = "figma"
    title = "Figma"
    settings = (
        Setting(
            "FIGMA_TOKEN",
            "Personal access token (Figma > Settings > Security) with 'File content: read-only' scope.",
        ),
        Setting(
            "FIGMA_FILE_KEY",
            "The file key from the file's URL: figma.com/design/<FILE_KEY>/...",
            secret=False,
        ),
        Setting(
            "FIGMA_NODE_IDS",
            "Comma-separated frame IDs to show (the node-id=12-34 part of a frame's link). "
            "Default: the top-level frames on the file's first page.",
            required=False,
            secret=False,
        ),
        Setting("FIGMA_IMAGE_SCALE", "Render scale, 0.01-4.", required=False, secret=False, default="2"),
        Setting(
            "FIGMA_CACHE_SECONDS",
            "How long renders are reused before asking Figma again.",
            required=False,
            secret=False,
            default="3600",
        ),
    )

    def __init__(self, environ=None):
        super().__init__(environ)
        self._cache: TTLCache | None = None

    @property
    def cache(self) -> TTLCache:
        if self._cache is None:
            self._cache = TTLCache(self.number("FIGMA_CACHE_SECONDS"))
        return self._cache

    def routes(self) -> list[Route]:
        return [
            Route(
                "/images",
                self.images,
                cache_control="public, max-age=300, s-maxage=3600, stale-while-revalidate=86400",
            )
        ]

    def public_info(self) -> dict:
        return {"embed_url": self.embed_url()}

    def embed_url(self) -> str:
        # For an interactive <iframe> instead of static images. The file must
        # be shared as "anyone with the link can view" for embeds to load.
        return f"https://embed.figma.com/design/{self.get('FIGMA_FILE_KEY')}?embed-host=agentbridge"

    def _get(self, path: str, params: dict | None = None) -> dict:
        data = http.request(
            "GET",
            f"{API}{path}",
            service=self.title,
            headers={"X-Figma-Token": self.require("FIGMA_TOKEN")},
            params=params,
            timeout=30,
        ).json()
        if data.get("err"):
            raise IntegrationError("Figma could not render the requested frames", detail=str(data["err"]))
        return data

    def _frames(self, file_key: str) -> tuple[dict, list[dict]]:
        """Returns (file info, [{"id", "name"}, ...]) for the frames to show."""
        configured = [i.replace("-", ":") for i in parse_list(self.get("FIGMA_NODE_IDS"))][:MAX_FRAMES]
        if configured:
            data = self._get(f"/files/{file_key}/nodes", {"ids": ",".join(configured)})
            frames = []
            for node_id in configured:
                node = (data.get("nodes") or {}).get(node_id) or {}
                doc = node.get("document")
                if doc:
                    frames.append({"id": node_id, "name": doc.get("name", "")})
                else:
                    current_app.logger.warning("FIGMA_NODE_IDS: node %s not found in file %s", node_id, file_key)
        else:
            data = self._get(f"/files/{file_key}", {"depth": "2"})
            pages = (data.get("document") or {}).get("children") or []
            children = pages[0].get("children", []) if pages else []
            frames = [
                {"id": c["id"], "name": c.get("name", "")}
                for c in children
                if c.get("type") in ("FRAME", "COMPONENT", "COMPONENT_SET", "SECTION")
            ][:MAX_FRAMES]
        return {"name": data.get("name"), "lastModified": data.get("lastModified")}, frames

    def images(self):
        def load() -> dict:
            file_key = self.require("FIGMA_FILE_KEY")
            info, frames = self._frames(file_key)
            urls = {}
            if frames:
                urls = self._get(
                    f"/images/{file_key}",
                    {
                        "ids": ",".join(f["id"] for f in frames),
                        "format": "png",
                        "scale": self.get("FIGMA_IMAGE_SCALE") or "2",
                    },
                ).get("images") or {}
            unrendered = [f["id"] for f in frames if not urls.get(f["id"])]
            if unrendered:
                current_app.logger.warning("Figma returned no render for frames %s (empty?)", ", ".join(unrendered))
            return {
                "file": info,
                "embed_url": self.embed_url(),
                # Figma returns null for a frame it couldn't render (e.g.
                # empty); skip those rather than showing a broken image.
                "images": [{**f, "url": urls[f["id"]]} for f in frames if urls.get(f["id"])],
            }

        return self.cache.get_or_set("images", load)

    def check(self) -> str:
        self.ensure_configured()
        data = self._get(f"/files/{self.require('FIGMA_FILE_KEY')}", {"depth": "1"})
        return f"can read file '{data.get('name')}'"
