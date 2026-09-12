"""Exercise the real hub container on an isolated GitHub runner."""

import json
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen


def node(roles):
    for _ in range(60):
        try:
            with urlopen("http://127.0.0.1:8088/api/v1/node/config", timeout=2) as response:
                value = json.load(response)
            if value["active_roles"] == roles:
                return value
        except (URLError, OSError):
            pass
        time.sleep(1)
    raise AssertionError(f"Container never became ready with roles {roles}")


initial = node([])
subprocess.run(
    ["docker", "exec", "tailcam-hub", "tailcam", "setup", "--preset", "storage"], check=True
)
subprocess.run(["docker", "restart", "tailcam-hub"], check=True)
updated = node(["storage"])
assert updated["node_id"] == initial["node_id"]
print("Hub starts without capture; saved role changes and UUID survive restart.")
