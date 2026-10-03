"""Live GitHub profile stats for the site: repos, stars, followers, top
languages, featured repos and (with a token) past-year contributions.

Everything here is public data. A token is optional but recommended on
Vercel: unauthenticated GitHub API calls are limited to 60/hour per IP,
and serverless egress IPs are shared with other people's apps.
"""

from flask import current_app

from . import http
from .base import Integration, IntegrationError, Route, Setting, TTLCache, parse_list

API = "https://api.github.com"
MAX_PAGES = 5  # 500 repos is plenty for stats; bounds the work per refresh
FEATURED_DEFAULT = 4
CONTRIBUTIONS_QUERY = """
query($login: String!) {
  user(login: $login) { contributionsCollection { contributionCalendar { totalContributions } } }
}
"""


class GitHubIntegration(Integration):
    name = "github"
    title = "GitHub"
    settings = (
        Setting("GITHUB_USERNAME", "Whose public stats to show.", secret=False),
        Setting(
            "GITHUB_TOKEN",
            "Optional fine-grained token with no extra permissions (public read only). Raises the "
            "rate limit and enables the past-year contributions count.",
            required=False,
        ),
        Setting(
            "GITHUB_FEATURED_REPOS",
            "Comma-separated repo names to feature. Default: your most-starred repos.",
            required=False,
            secret=False,
        ),
        Setting(
            "GITHUB_CACHE_SECONDS",
            "How long stats are reused before asking GitHub again.",
            required=False,
            secret=False,
            default="600",
        ),
    )

    def __init__(self, environ=None):
        super().__init__(environ)
        self._cache: TTLCache | None = None

    @property
    def cache(self) -> TTLCache:
        if self._cache is None:
            self._cache = TTLCache(self.number("GITHUB_CACHE_SECONDS"))
        return self._cache

    def routes(self) -> list[Route]:
        return [
            Route(
                "/stats",
                self.stats,
                cache_control="public, max-age=300, s-maxage=600, stale-while-revalidate=86400",
            )
        ]

    def public_info(self) -> dict:
        return {"username": self.get("GITHUB_USERNAME")}

    def _headers(self) -> dict:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "agentbridge-site",
        }
        token = self.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _get(self, path: str, params: dict | None = None):
        return http.request("GET", f"{API}{path}", service=self.title, headers=self._headers(), params=params).json()

    def _repos(self, user: str) -> list[dict]:
        repos = []
        for page in range(1, MAX_PAGES + 1):
            batch = self._get(
                f"/users/{user}/repos", {"per_page": "100", "page": str(page), "type": "owner", "sort": "pushed"}
            )
            repos.extend(batch)
            if len(batch) < 100:
                break
        return repos

    def _contributions(self, user: str) -> int | None:
        """Past-year contribution count. GitHub only exposes it via GraphQL,
        which requires a token. Best-effort: a failure here drops this one
        number rather than the whole stats response."""
        if not self.get("GITHUB_TOKEN"):
            return None
        try:
            data = http.request(
                "POST",
                f"{API}/graphql",
                service=self.title,
                headers=self._headers(),
                json_body={"query": CONTRIBUTIONS_QUERY, "variables": {"login": user}},
            ).json()
            return data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["totalContributions"]
        except (IntegrationError, KeyError, TypeError) as e:
            current_app.logger.warning("github contributions unavailable: %s", getattr(e, "detail", None) or e)
            return None

    def stats(self):
        def load() -> dict:
            user = self.require("GITHUB_USERNAME")
            profile = self._get(f"/users/{user}")
            # Forks would inflate stars/languages with other people's work.
            own = [r for r in self._repos(user) if not r.get("fork")]

            languages: dict[str, int] = {}
            for r in own:
                if r.get("language"):
                    languages[r["language"]] = languages.get(r["language"], 0) + 1

            wanted = [n.lower() for n in parse_list(self.get("GITHUB_FEATURED_REPOS"))]
            if wanted:
                by_name = {r["name"].lower(): r for r in own}
                featured = [by_name[n] for n in wanted if n in by_name]
            else:
                candidates = [r for r in own if not r.get("archived")]
                featured = sorted(
                    candidates, key=lambda r: (r.get("stargazers_count", 0), r.get("pushed_at") or ""), reverse=True
                )[:FEATURED_DEFAULT]

            return {
                "profile": {
                    "login": profile.get("login"),
                    "name": profile.get("name"),
                    "avatar_url": profile.get("avatar_url"),
                    "html_url": profile.get("html_url"),
                },
                "totals": {
                    "public_repos": profile.get("public_repos", len(own)),
                    "stars": sum(r.get("stargazers_count", 0) for r in own),
                    "followers": profile.get("followers", 0),
                    "contributions_last_year": self._contributions(user),
                },
                "languages": [
                    {"name": name, "repos": count}
                    for name, count in sorted(languages.items(), key=lambda kv: (-kv[1], kv[0]))[:6]
                ],
                "featured": [
                    {
                        "name": r["name"],
                        "description": r.get("description"),
                        "url": r.get("html_url"),
                        "stars": r.get("stargazers_count", 0),
                        "forks": r.get("forks_count", 0),
                        "language": r.get("language"),
                        "pushed_at": r.get("pushed_at"),
                    }
                    for r in featured
                ],
            }

        return self.cache.get_or_set("stats", load)

    def check(self) -> str:
        self.ensure_configured()
        profile = self._get(f"/users/{self.require('GITHUB_USERNAME')}")
        rate = self._get("/rate_limit").get("rate", {})
        return (
            f"found {profile.get('login')} ({profile.get('public_repos')} public repos); "
            f"API quota {rate.get('remaining')}/{rate.get('limit')} remaining"
            + ("" if self.get("GITHUB_TOKEN") else " -- set GITHUB_TOKEN for a higher limit")
        )
