# TailCam plugins

A plugin contributes a **provider** for a capability. Today two kinds ship:

- **AI analyzer providers** — an alternative to the built-in Ollama analyzer.
- **Notification channels** — an alternative to Discord/Telegram/webhook.

A plugin is any module exposing `@hookimpl`-decorated functions for the
hookspecs in `tailcam.plugins.hookspecs`. TailCam discovers plugins with
[pluggy](https://pluggy.readthedocs.io).

## Two ways to ship a plugin

### 1. Drop-in (easiest — no packaging)

Copy a single `.py` file into your TailCam config dir's `plugins/` folder and
restart:

```bash
cp ntfy_channel.py ~/.config/tailcam/plugins/
tailcam restart    # or restart `tailcam run`
```

See [`ntfy_channel.py`](ntfy_channel.py) for a complete example (an ntfy.sh
notification channel).

### 2. Pip package (entry point)

Publish/install a package that registers under the `tailcam` entry-point group —
then `pip install` it anywhere TailCam runs:

```toml
# pyproject.toml of your plugin package
[project]
name = "tailcam-plugin-slack"
version = "0.1.0"
dependencies = ["httpx"]

[project.entry-points.tailcam]
slack = "tailcam_plugin_slack"   # module exposing the @hookimpl functions

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

```bash
pip install tailcam-plugin-slack
```

## The interfaces

```python
# A notification channel
class MyChannel:
    id = "mychan"          # stable id
    name = "My channel"    # display name
    def configured(self, config) -> bool: ...   # is it set up? (config = NotificationsConfig)
    def send(self, event, config) -> None: ...   # event = NotificationEvent

# An AI analyzer provider
class MyProvider:
    id = "myai"
    name = "My AI"
    description = "…"
    def build(self, config):   # config = AIConfig
        return SomeFrameAnalyzer(...)   # implements .enabled and .analyze(image)
```

Register them:

```python
from tailcam.plugins.hookspecs import hookimpl, PluginInfo

@hookimpl
def tailcam_notification_channels():
    return [MyChannel()]

@hookimpl
def tailcam_analyzer_providers():
    return [MyProvider()]

@hookimpl
def tailcam_plugin_info():
    return [PluginInfo(id="mychan", name="My channel", kind="notification", description="…")]
```

## Verify

```bash
tailcam plugins      # lists everything discovered
```

…or open **Settings → Plugins** in the dashboard. To use a non-Ollama AI
provider, set `[ai] provider = "<id>"` in your config (or via the API) and
restart.
