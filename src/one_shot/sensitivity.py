from __future__ import annotations

import json
from enum import Enum
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from typing import Dict, Any, Optional


class SensitivityLevel(str, Enum):
    SAFE = "safe"
    SENSITIVE = "sensitive"
    UNKNOWN = "unknown"


def _parse_github_repo(repo_url: str) -> Optional[tuple[str, str]]:
    parsed = urlparse(repo_url)
    if parsed.scheme != "https":
        return None
    host = parsed.netloc.lower()
    if host != "github.com":
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def _github_repo_visibility(owner: str, repo: str, token: Optional[str]) -> SensitivityLevel:
    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {
        "User-Agent": "oneshot-bench/1.0",
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = f"token {token}"
    request = Request(api_url, headers=headers)
    try:
        with urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode())
    except HTTPError as err:
        # 404/403 with a token strongly suggests the repository is private or inaccessible.
        if token and err.code in (401, 403, 404):
            return SensitivityLevel.SENSITIVE
        return SensitivityLevel.UNKNOWN
    except URLError:
        return SensitivityLevel.UNKNOWN

    private = data.get("private")
    visibility = data.get("visibility")
    if private is True or visibility == "private":
        return SensitivityLevel.SENSITIVE
    if private is False or visibility == "public":
        return SensitivityLevel.SAFE
    return SensitivityLevel.UNKNOWN


def detect_repo_sensitivity(repo_url: str, token: Optional[str] = None) -> SensitivityLevel:
    """Infer repository sensitivity based on the remote URL."""
    parsed = _parse_github_repo(repo_url)
    if parsed is None:
        return SensitivityLevel.UNKNOWN
    owner, repo = parsed
    return _github_repo_visibility(owner, repo, token)


def ensure_task_sensitivity(task_meta: Dict[str, Any], token: Optional[str] = None) -> SensitivityLevel:
    """Ensure task metadata contains a sensitivity label."""
    existing = (task_meta.get("sensitivity") or {}).get("level")
    if existing in {level.value for level in SensitivityLevel}:
        return SensitivityLevel(existing)

    repo_url = (task_meta.get("repo") or {}).get("git_url")
    if repo_url:
        level = detect_repo_sensitivity(repo_url, token)
    else:
        level = SensitivityLevel.UNKNOWN

    task_meta["sensitivity"] = {"level": level.value}
    return level


def is_sensitive_task(task_meta: Dict[str, Any]) -> bool:
    sensitivity = (task_meta.get("sensitivity") or {}).get("level", "").lower()
    return sensitivity == SensitivityLevel.SENSITIVE.value
