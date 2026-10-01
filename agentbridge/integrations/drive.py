"""Serve files straight from Google Drive, so updating e.g. your resume in
Drive updates the site with no redeploy.

Only files you list in DRIVE_FILES are reachable, by alias:
  DRIVE_FILES="resume=1AbCdEf..."  ->  GET /api/integrations/drive/files/resume

Visitors can never pass a raw Drive file ID. Otherwise anyone could fetch
any file that happens to be shared with the service account.
"""

import re

from flask import Response, request

from . import google, http
from .base import Integration, IntegrationError, Route, Setting, TTLCache, parse_mapping

DRIVE_API = "https://www.googleapis.com/drive/v3/files"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
# Vercel caps a serverless function's response body at 4.5 MB.
MAX_FILE_BYTES = 4 * 1024 * 1024
# Native Google formats have no bytes of their own and must be exported.
EXPORT_FORMATS = {
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.drawing": ("image/png", ".png"),
}
INLINE_SAFE_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp", "text/plain"}


class DriveIntegration(Integration):
    name = "drive"
    title = "Google Drive"
    settings = (
        Setting(
            "GOOGLE_SERVICE_ACCOUNT_JSON",
            "Service account key JSON (raw or base64). Share each file with the service account's client_email.",
        ),
        Setting(
            "DRIVE_FILES",
            "Comma-separated alias=fileId pairs, e.g. resume=1AbCdEf... (the ID is in the file's share URL).",
            secret=False,
        ),
        Setting(
            "DRIVE_CACHE_SECONDS",
            "How long a fetched file is reused before re-checking Drive.",
            required=False,
            secret=False,
            default="300",
        ),
    )

    def __init__(self, environ=None):
        super().__init__(environ)
        self._cache: TTLCache | None = None

    @property
    def cache(self) -> TTLCache:
        if self._cache is None:
            self._cache = TTLCache(float(self.get("DRIVE_CACHE_SECONDS") or 300))
        return self._cache

    def files(self) -> dict[str, str]:
        return parse_mapping(self.get("DRIVE_FILES"))

    def public_info(self) -> dict:
        return {"files": sorted(self.files())}

    def routes(self) -> list[Route]:
        return [
            Route("/files/<alias>", self.serve_file),
            Route("/files/<alias>/meta", self.file_meta, cache_control="public, max-age=60, s-maxage=300"),
        ]

    def _token(self) -> str:
        info = google.parse_service_account(self.require("GOOGLE_SERVICE_ACCOUNT_JSON"))
        return google.service_account_token(info, SCOPES)

    def _file_id(self, alias: str) -> str:
        file_id = self.files().get(alias)
        if not file_id:
            raise IntegrationError(f"No Drive file is published as '{alias}'", status=404)
        return file_id

    def _metadata(self, file_id: str) -> dict:
        return http.request(
            "GET",
            f"{DRIVE_API}/{file_id}",
            service=self.title,
            headers={"Authorization": f"Bearer {self._token()}"},
            params={"fields": "id,name,mimeType,modifiedTime,size", "supportsAllDrives": "true"},
        ).json()

    def fetch(self, alias: str) -> dict:
        """Returns {"name", "mime", "modified", "body"} for a published alias."""
        file_id = self._file_id(alias)

        def load() -> dict:
            meta = self._metadata(file_id)
            auth = {"Authorization": f"Bearer {self._token()}"}
            name = meta.get("name") or alias
            mime = meta.get("mimeType", "application/octet-stream")
            if mime in EXPORT_FORMATS:
                export_mime, ext = EXPORT_FORMATS[mime]
                resp = http.request(
                    "GET",
                    f"{DRIVE_API}/{file_id}/export",
                    service=self.title,
                    headers=auth,
                    params={"mimeType": export_mime},
                    max_bytes=MAX_FILE_BYTES,
                )
                mime, name = export_mime, name if name.lower().endswith(ext) else name + ext
            elif mime.startswith("application/vnd.google-apps."):
                raise IntegrationError(
                    "That Drive item can't be downloaded (folders and forms aren't files)",
                    status=422,
                    detail=f"unsupported Google mime type {mime}",
                )
            else:
                resp = http.request(
                    "GET",
                    f"{DRIVE_API}/{file_id}",
                    service=self.title,
                    headers=auth,
                    params={"alt": "media", "supportsAllDrives": "true"},
                    max_bytes=MAX_FILE_BYTES,
                    timeout=30,
                )
            return {"name": name, "mime": mime, "modified": meta.get("modifiedTime"), "body": resp.body}

        return self.cache.get_or_set(alias, load)

    def serve_file(self, alias: str):
        f = self.fetch(alias)
        # Files are served from the backend's own origin, which also hosts
        # the dashboard and its session cookie. Only display types that
        # can't run script inline; anything else (an .html or .svg someone
        # uploads) is forced to download instead of rendering as a page.
        inline_ok = f["mime"] in INLINE_SAFE_TYPES
        disposition = "attachment" if request.args.get("download") or not inline_ok else "inline"
        return Response(
            f["body"],
            mimetype=f["mime"],
            headers={
                "Content-Disposition": f'{disposition}; filename="{_safe_filename(f["name"])}"',
                # Lets Vercel's CDN serve repeat visitors without invoking
                # the function at all, while picking up Drive edits within
                # a few minutes.
                "Cache-Control": "public, max-age=60, s-maxage=300, stale-while-revalidate=86400",
                "X-Content-Type-Options": "nosniff",
            },
        )

    def file_meta(self, alias: str):
        f = self.fetch(alias)
        return {"alias": alias, "name": f["name"], "mimeType": f["mime"], "modifiedTime": f["modified"]}

    def check(self) -> str:
        self.ensure_configured()
        token = self._token()
        names = []
        for alias, file_id in self.files().items():
            meta = http.request(
                "GET",
                f"{DRIVE_API}/{file_id}",
                service=self.title,
                headers={"Authorization": f"Bearer {token}"},
                params={"fields": "name", "supportsAllDrives": "true"},
            ).json()
            names.append(f"{alias} -> {meta.get('name')}")
        if not names:
            raise IntegrationError("DRIVE_FILES has no alias=fileId pairs", status=500)
        return "can read " + ", ".join(names)


def _safe_filename(name: str) -> str:
    """ASCII-only and quote-free, so it can't break the header it's placed in."""
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip() or "file"
    return cleaned[:150]
