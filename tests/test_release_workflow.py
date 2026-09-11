"""Release trust, version and retry gates, including remote failures."""

import importlib.util
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

path = Path(__file__).parents[1] / ".github/scripts/release_check.py"
spec = importlib.util.spec_from_file_location("release_check", path)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def event():
    return {
        "workflow_run": {
            "event": "push",
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": "tested",
            "head_repository": {"full_name": "FactShin/TailCam"},
        }
    }


def test_successful_main_run_and_manual():
    release.check_source("workflow_run", event(), "tested", "tested", "1.9.1")
    release.check_source("workflow_dispatch", {}, "tested", "tested", "1.9.1")


@pytest.mark.parametrize(
    "field,value",
    [
        ("event", "pull_request"),
        ("conclusion", "failure"),
        ("head_branch", "feature"),
        ("head_sha", "different"),
        ("head_repository", {"full_name": "attacker/TailCam"}),
    ],
)
def test_untrusted_or_failed_workflow_cannot_publish(field, value):
    data = event()
    data["workflow_run"][field] = value
    with pytest.raises(ValueError):
        release.check_source("workflow_run", data, "tested", "tested", "1.9.1")


def test_stale_commit_or_mismatched_tag_cannot_publish():
    with pytest.raises(ValueError):
        release.check_source("workflow_dispatch", {}, "stale", "tested", "1.9.1")
    with pytest.raises(ValueError):
        release.check_source(
            "release", {"release": {"tag_name": "v1.8.4"}}, "tested", "tested", "1.9.1"
        )


def test_checks_are_bound_to_exact_main_commit():
    runs = {
        "workflow_runs": [
            {
                "head_sha": "tested",
                "event": "push",
                "head_branch": "main",
                "status": "completed",
                "conclusion": "success",
            }
        ]
    }
    assert release.tested(runs, "tested")
    assert not release.tested(runs, "different")
    runs["workflow_runs"][0]["conclusion"] = "failure"
    assert not release.tested(runs, "tested")


def test_published_complete_release_is_skipped(monkeypatch):
    monkeypatch.setattr(
        release,
        "read_json",
        lambda url: {"urls": [{"packagetype": "sdist"}, {"packagetype": "bdist_wheel"}]},
    )
    assert release.already_published("1.9.1")


@pytest.mark.parametrize("code", [404, 403, 500])
def test_only_not_found_means_new_release(monkeypatch, code):
    def response(url):
        raise HTTPError(url, code, "error", {}, None)

    monkeypatch.setattr(release, "read_json", response)
    if code == 404:
        assert not release.already_published("1.9.1")
    else:
        with pytest.raises(HTTPError):
            release.already_published("1.9.1")


def test_connection_failure_and_partial_release_stop_publish(monkeypatch):
    def offline(url):
        raise URLError("offline")

    monkeypatch.setattr(release, "read_json", offline)
    with pytest.raises(URLError):
        release.already_published("1.9.1")
    monkeypatch.setattr(release, "read_json", lambda url: {"urls": [{"packagetype": "sdist"}]})
    with pytest.raises(ValueError):
        release.already_published("1.9.1")


def test_version_read_without_importing_runtime():
    import tailcam

    assert release.package_version(path.parents[2]) == tailcam.__version__


def test_publish_identity_is_separate_from_repository_execution():
    import yaml

    workflow = yaml.load(
        (path.parents[1] / "workflows/pypi-publish.yml").read_text(), Loader=yaml.BaseLoader,
    )
    assert workflow["on"]["workflow_run"]["branches"] == ["main"]
    jobs = workflow["jobs"]
    assert "id-token" not in jobs["build"]["permissions"]
    assert jobs["pypi"]["permissions"] == {"id-token": "write"}
    assert all("run" not in step for step in jobs["pypi"]["steps"])
    assert jobs["pypi"]["environment"]["name"] == "pypi"
    assert jobs["verify"]["permissions"] == {}
