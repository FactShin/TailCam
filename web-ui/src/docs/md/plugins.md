# Plugins

Plugins extend TailCam with things that aren't the base app: **AI analyzer
providers**, **notification channels**, and **motion-event hooks**
(automation). Everything runs locally, and there are two ways to get one:

1. **The marketplace** — open **Plugins** in the nav, browse the curated
   registry, click **Install**. The plugin downloads, is verified, and is live
   immediately (no restart). Updates show up as an **Update** button.
2. **Drop-in / pip** — copy a single `.py` file into your config folder's
   `plugins/` directory and hit **Reload** on the Plugins page, or
   `pip install` a package exposing the `tailcam` entry-point group.

Enable/disable and uninstall live on the same page. Disabled plugins stay on
disk but their code is never loaded.

## Security model — read this once

A plugin is ordinary Python running **with the full privileges of the TailCam
process**. There is no sandbox. What protects you:

- **Curation** — the default registry is the TailCam repository's
  `marketplace/` folder; every plugin gets human review (readable code, scoped
  network access, no dynamic code loading) before it's listed.
- **Verification** — the registry pins each file's **sha256**; installs verify
  the checksum, a 1 MB size cap, and Python syntax before anything touches
  disk. A mismatch aborts the install.
- **Explicit action** — nothing installs, runs, or updates without you
  clicking it.

Only install plugins you trust, exactly like browser extensions. Fleet
operators can point `plugins.registry_url` at a private index to control what
their nodes can see.

## Configuring a plugin

Plugins read their own settings from your `config.toml`, under
`[plugins.settings.<id>]` — each marketplace card shows a ready-to-paste
example. For instance:

```toml
[plugins.settings.ntfy]
topic = "my-tailcam-alerts"
```

Secrets (API keys, webhook URLs) stay in your config file on your machine —
plugins never bundle credentials.

## Writing your own plugin

One Python file is a complete plugin. The whole SDK is one import:

```python
"""My plugin — one line about what it does."""

__plugin__ = {
    "id": "my_plugin",            # must equal the file stem
    "name": "My plugin",
    "version": "1.0.0",
    "description": "One sentence for the marketplace.",
    "author": "you",
    "kinds": ["notification"],    # ai | notification | event
}

from tailcam.plugins.sdk import PluginInfo, hookimpl, plugin_settings

class MyChannel:
    id = "my_plugin"
    name = "My channel"

    def configured(self, config):
        return bool(plugin_settings("my_plugin").get("url"))

    def send(self, event, config):
        ...  # POST event.title / event.body somewhere; catch your own errors

@hookimpl
def tailcam_notification_channels():
    return [MyChannel()]

@hookimpl
def tailcam_plugin_info():
    return [PluginInfo(id="my_plugin", name="My plugin", kind="notification")]
```

Drop it in `<config-dir>/plugins/`, press **Reload** on the Plugins page, and
it's running — load errors are shown right there.

### The three capability hooks

| Hook | You provide | Used for |
| --- | --- | --- |
| `tailcam_analyzer_providers` | `.id`, `.name`, `.build(ai_config)` → object with `.enabled` + `.analyze(image)` | AI motion labeling backends (select with `[ai] provider = "<id>"`, restart) |
| `tailcam_notification_channels` | `.id`, `.name`, `.configured(cfg)`, `.send(event, cfg)` | Alert destinations, honoring the user's notification filters |
| `tailcam_event_hooks` | `.id`, `.name`, `.on_motion(event)` | Automation on **every** motion event (lights, sirens, logs…) |

Ground rules: one file, only stdlib + `tailcam` + its bundled deps (`httpx`,
`cv2`, `numpy`), catch your own exceptions (a dead integration must never
break detection), read settings via `plugin_settings("<id>")`.

The heavily-commented starting point lives at
[`marketplace/TEMPLATE.py`](https://github.com/FactShin/TailCam/blob/main/marketplace/TEMPLATE.py),
and [`event_logger`](https://github.com/FactShin/TailCam/blob/main/marketplace/plugins/event_logger.py)
is a complete real example.

## Publishing to the marketplace

1. Fork the TailCam repository and add your file under `marketplace/plugins/`.
2. Run `python marketplace/build_index.py` (regenerates `index.json` with your
   file's sha256).
3. Open a pull request. Reviewers check the criteria in
   [`marketplace/README.md`](https://github.com/FactShin/TailCam/blob/main/marketplace/README.md) —
   readable, scoped, resilient, honest.

Once merged, every TailCam node's Plugins page offers your plugin; version
bumps show users an Update button.

## CLI

```bash
tailcam plugins                        # list loaded plugins + capabilities
tailcam plugin-install ntfy_notifier   # install from the registry
tailcam plugin-remove ntfy_notifier    # uninstall
```
