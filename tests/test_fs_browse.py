"""Folder picker API: folders only, roots when blank, graceful errors."""


def test_browse_roots_and_folders(client, tmp_path):
    tmp_path = tmp_path / "root"
    tmp_path.mkdir()
    (tmp_path / "usb").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "file.txt").write_text("x")
    roots = client.get("/api/fs/browse").json()
    assert roots["path"] == "" and roots["roots"]
    assert any(r["path"] for r in roots["roots"])

    listing = client.get("/api/fs/browse", params={"path": str(tmp_path)}).json()
    names = [e["name"] for e in listing["entries"]]
    assert names == ["usb"]  # files and dot-folders are not listed
    assert listing["exists"] and listing["writable"] and listing["disk_total"] > 0
    assert listing["parent"] == str(tmp_path.parent)

    hidden = client.get(
        "/api/fs/browse", params={"path": str(tmp_path), "show_hidden": "true"}
    ).json()
    assert ".hidden" in [e["name"] for e in hidden["entries"]]


def test_browse_missing_and_relative(client, tmp_path):
    missing = client.get("/api/fs/browse", params={"path": str(tmp_path / "nope")}).json()
    assert missing["exists"] is False and "does not exist" in missing["error"]
    assert missing["parent"] == str(tmp_path)
    rel = client.get("/api/fs/browse", params={"path": "relative/dir"}).json()
    assert rel["exists"] is False and "absolute" in rel["error"]
