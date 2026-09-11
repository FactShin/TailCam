"""Fail closed before building a tested release; never import the package."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def read_json(url: str, token: str | None = None) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "TailCam-release"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        return json.load(response)


def package_version(root: Path) -> str:
    tree = ast.parse((root / "src/tailcam/__init__.py").read_text(encoding="utf-8"))
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in statement.targets
        ):
            version = ast.literal_eval(statement.value)
            if isinstance(version, str) and re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", version):
                return version
    raise ValueError("Package must declare a numeric release version")


def check_source(event_name: str, event: dict, sha: str, main_sha: str, version: str) -> None:
    if event_name == "workflow_run":
        run = event["workflow_run"]
        if not (
            run["event"] == "push"
            and run["conclusion"] == "success"
            and run["head_branch"] == "main"
            and run["head_sha"] == sha
            and run["head_repository"]["full_name"] == "FactShin/TailCam"
        ):
            raise ValueError("Only successful main push tests may publish automatically")
    elif event_name == "release":
        if event["release"]["tag_name"] != f"v{version}":
            raise ValueError("Release tag does not match package version")
        if event["release"].get("draft") or event["release"].get("prerelease"):
            raise ValueError("Only stable published releases are supported")
    elif event_name != "workflow_dispatch":
        raise ValueError("Unsupported release trigger")
    if sha != main_sha:
        raise ValueError("Release commit is not current main; run from main after tests pass")


def tested(runs: dict, sha: str) -> bool:
    return any(
        run.get("head_sha") == sha
        and run.get("event") == "push"
        and run.get("head_branch") == "main"
        and run.get("conclusion") == "success"
        and run.get("status") == "completed"
        for run in runs["workflow_runs"]
    )


def already_published(version: str) -> bool:
    try:
        release = read_json(f"https://pypi.org/pypi/tailcam/{version}/json")
    except HTTPError as exc:
        if exc.code == 404:
            return False
        raise
    files = release["urls"]
    kinds = {item["packagetype"] for item in files if not item.get("yanked")}
    if not {"sdist", "bdist_wheel"} <= kinds:
        raise ValueError("PyPI version is incomplete or yanked; inspect before retrying")
    return True


def main() -> None:
    version = package_version(Path.cwd())
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    token = os.environ["GH_TOKEN"]
    base = "https://api.github.com/repos/FactShin/TailCam"
    head = read_json(f"{base}/git/ref/heads/main", token)["object"]["sha"]
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    check_source(os.environ["GITHUB_EVENT_NAME"], event, sha, head, version)
    runs = read_json(f"{base}/actions/workflows/tests.yml/runs?head_sha={sha}&event=push", token)
    if not tested(runs, sha):
        raise ValueError("No successful main test workflow for the exact release commit")
    publish = not already_published(version)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"version={version}\npublish={str(publish).lower()}\n")
    print(f"TailCam {version}: {'ready to publish' if publish else 'already on PyPI; skipped'}")


if __name__ == "__main__":
    main()
