# Plugins

TailCam is extensible through **plugins** — small add-ons that contribute extra
**AI analyzer providers** or **notification channels** without forking the app.
The built-in Ollama analyzer and the Discord/Telegram/webhook channels are
themselves plugins, so third-party ones plug in exactly the same way.

Discovery uses [pluggy](https://pluggy.readthedocs.io). See your installed
plugins in **Settings → Plugins** or with `tailcam plugins`.

## Add a plugin — two ways

### Drop-in (no packaging)
Copy a single `.py` file into your config folder's `plugins/` directory and
restart:

```bash
cp ntfy_channel.py ~/.config/tailcam/plugins/
tailcam restart
```

### Pip package (entry point)
Install any package that registers under the `tailcam` entry-point group:

```bash
pip install tailcam-plugin-slack
```

Either way it shows up in **Settings → Plugins** after a restart.

## What a plugin provides

- **A notification channel** — `id`, `name`, `configured(config)`, `send(event,
  config)`. The event carries the title, body, severity, and structured data;
  your channel decides how to deliver it.
- **An AI analyzer provider** — `id`, `name`, `description`, and `build(config)`
  returning an analyzer (anything with `.enabled` and `.analyze(image)`).

A plugin registers them with `@hookimpl` functions
(`tailcam_notification_channels`, `tailcam_analyzer_providers`,
`tailcam_plugin_info`). A complete, copyable example (an ntfy.sh channel) lives
in `examples/plugins/`.

## Choosing an AI provider

`ollama` is the default. To use a provider from a plugin, set the analyzer
provider — in `[ai]`:

```toml
[ai]
provider = "myai"
```

…or via the AI section / API, then restart. Ollama-specific features (the model
catalog, in-UI download) apply only when the Ollama provider is active.

## Managing plugins

| Where | What |
| --- | --- |
| `tailcam plugins` | List discovered plugins, providers, and channels. |
| Settings → Plugins | The same, in the dashboard. |
| `[plugins] disabled` | Ids to skip loading. |
| `[plugins] load_dropins` | Toggle the drop-in folder. |

> Plugins run in-process with full access to your machine — only install ones you
> trust, the same as any Python package.
