"""MCP tool definitions and handlers.

Tools use stable lower_snake_case names. Each returns a :class:`ToolResult` with
a short human ``summary`` and a machine ``data`` body; the server turns that into
MCP ``content`` + ``structuredContent``. Failures raise
:class:`~tailcam.mcp.errors.TailcamMcpError`, which the server renders as an
``isError`` result with a normalized error envelope.

Authorization is declarative: every tool carries a ``min_role`` and a ``write``
flag. The server filters ``tools/list`` by the caller's role and audits writes.
Confirmation rules live in the handlers (closest to the dangerous action) and are
master-switched by the ``[mcp]`` config.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from tailcam.mcp import errors
from tailcam.mcp.errors import TailcamMcpError
from tailcam.mcp.toolctx import ToolContext
from tailcam.security.principal import TailCamRole

Handler = Callable[[ToolContext, dict[str, Any]], Awaitable["ToolResult"]]


@dataclass
class ToolResult:
    summary: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler
    min_role: TailCamRole = TailCamRole.VIEWER
    write: bool = False


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


_NO_INPUT = _obj({})


def _media_url(prefix: str, kind: str, ident: Any, *, thumb: bool = False) -> str:
    leaf = "thumbnail" if thumb else "file"
    if kind == "event":
        return f"{prefix}/events/{ident}/thumbnail"
    return f"{prefix}/media/{ident}/{leaf}"


def _require_confirm(args: dict[str, Any], enabled: bool) -> None:
    if enabled and args.get("confirm") is not True:
        raise TailcamMcpError(
            errors.CONFIRMATION_REQUIRED,
            "This action changes state; resend with confirm=true.",
        )


def _require_scope(args: dict[str, Any], expected: str, enabled: bool) -> None:
    if not enabled:
        return
    got = (args.get("confirm_scope") or "").strip()
    if got != expected:
        raise TailcamMcpError(
            errors.CONFIRMATION_REQUIRED,
            f'This action requires confirm_scope="{expected}".',
        )


def _camera_line(cam: dict[str, Any]) -> str:
    flags = []
    if cam.get("recording"):
        flags.append("rec")
    if cam.get("motion_enabled"):
        flags.append("motion")
    suffix = f" [{','.join(flags)}]" if flags else ""
    host = cam.get("host") or "local"
    return f"- {cam.get('id')} ({cam.get('name')}) on {host}: {cam.get('status')}{suffix}"


# --------------------------------------------------------------------------
# read tools
# --------------------------------------------------------------------------
async def _get_system_status(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    system = await ctx.client.system()
    ts = "running" if system.get("tailscale_running") else "stopped"
    mb = system.get("media_bytes", 0) / (1024 * 1024)
    summary = (
        f"{system.get('host', 'node')} v{system.get('version')} · tailscale {ts} · "
        f"{mb:.0f} MB media · access {system.get('access_url') or 'n/a'}"
    )
    return ToolResult(summary, {"ok": True, "system": system})


async def _list_fleet_nodes(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    hosts = await ctx.client.hosts()
    lines = [
        f"- {h.get('node_key')}: {h.get('host')} v{h.get('version') or '?'} "
        f"({'online' if h.get('online') else 'offline'}, {h.get('camera_count', 0)} cams)"
        for h in hosts
    ]
    summary = f"{len(hosts)} node(s):\n" + "\n".join(lines)
    return ToolResult(summary, {"ok": True, "nodes": hosts})


async def _get_node_health(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    node_key = _str_arg(args, "node_key")
    health = await ctx.client.node_health(node_key)
    issues = health.get("issues", [])
    summary = (
        f"{health.get('host')} v{health.get('version')}: "
        f"{health.get('camera_online')}/{health.get('camera_total')} cameras online, "
        f"{len(issues)} issue(s)"
    )
    return ToolResult(summary, {"ok": True, "health": health})


async def _list_cameras(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    scope = args.get("scope", "all")
    cams = await ctx.client.cameras(scope=scope)
    node_key = args.get("node_key")
    if node_key:
        cams = [c for c in cams if (c.get("host") or "").startswith(str(node_key))]
    summary = f"{len(cams)} camera(s):\n" + "\n".join(_camera_line(c) for c in cams)
    return ToolResult(summary, {"ok": True, "cameras": cams})


async def _inspect_camera(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    camera_id = _str_arg(args, "camera_id")
    cam = await ctx.client.camera(camera_id)
    prefix = cam.get("proxy_prefix", "")
    hints = {
        "snapshot_url": f"{prefix}/stream/{camera_id}/snapshot.jpg",
        "mjpeg_url": f"{prefix}/stream/{camera_id}.mjpg",
    }
    err = f" · error: {cam.get('last_error')}" if cam.get("last_error") else ""
    summary = (
        f"{cam.get('id')} ({cam.get('name')}): {cam.get('status')} "
        f"{cam.get('width')}x{cam.get('height')}@{cam.get('fps')}fps{err}"
    )
    return ToolResult(summary, {"ok": True, "camera": cam, "stream_hints": hints})


async def _list_recent_events(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    limit = _clamp(args.get("limit", 25), 1, ctx.config.max_events)
    events = await ctx.client.events(
        limit=limit, camera_id=args.get("camera_id"), scope=args.get("scope", "all")
    )
    for ev in events:
        if ev.get("has_thumb"):
            ev["thumbnail_url"] = _media_url(
                ev.get("proxy_prefix", ""), "event", ev.get("id")
            )
    lines = [
        f"- #{ev.get('id')} {ev.get('camera_id')}: "
        f"{ev.get('label') or 'motion'} ({ev.get('confidence') or ev.get('peak_score')})"
        for ev in events[:10]
    ]
    summary = f"{len(events)} event(s):\n" + "\n".join(lines)
    return ToolResult(summary, {"ok": True, "events": events})


async def _list_recent_media(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    limit = _clamp(args.get("limit", 25), 1, ctx.config.max_media)
    media = await ctx.client.media(
        limit=limit,
        camera_id=args.get("camera_id"),
        media_type=args.get("media_type"),
        scope=args.get("scope", "all"),
    )
    for m in media:
        m["file_url"] = _media_url(m.get("proxy_prefix", ""), "media", m.get("id"))
        if m.get("has_thumbnail"):
            m["thumbnail_url"] = _media_url(
                m.get("proxy_prefix", ""), "media", m.get("id"), thumb=True
            )
    total_mb = sum(m.get("size_bytes", 0) for m in media) / (1024 * 1024)
    summary = f"{len(media)} item(s), {total_mb:.1f} MB total."
    return ToolResult(summary, {"ok": True, "media": media})


async def _get_audit_log(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    node_key = args.get("node_key", "local")
    limit = _clamp(args.get("limit", 50), 1, 500)
    offset = _clamp(args.get("offset", 0), 0, 1_000_000)
    records = await ctx.client.node_audit(node_key, limit=limit, offset=offset)
    lines = [
        f"- {r.get('action')} by {r.get('actor')} -> {r.get('result')} ({r.get('target')})"
        for r in records[:15]
    ]
    summary = f"{len(records)} audit record(s):\n" + "\n".join(lines)
    return ToolResult(summary, {"ok": True, "audit": records})


async def _get_ai_status(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    ai = await ctx.client.ai()
    training = await ctx.client.training()
    state = "enabled" if ai.get("enabled") else "disabled"
    reach = "reachable" if ai.get("reachable") else "unreachable"
    summary = (
        f"AI {state} ({reach}), model {ai.get('model')} "
        f"{'present' if ai.get('model_present') else 'missing'}; "
        f"training engine {'available' if training.get('engine_available') else 'unavailable'}"
    )
    return ToolResult(summary, {"ok": True, "ai": ai, "training": training})


# --------------------------------------------------------------------------
# camera action tools
# --------------------------------------------------------------------------
async def _capture_snapshot(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    camera_id = _str_arg(args, "camera_id")
    result = await ctx.client.snapshot(camera_id)
    media_id = result.get("media_id")
    data = {
        "ok": True,
        "media_id": media_id,
        "file_url": _media_url("", "media", media_id) if media_id else None,
        "camera_id": camera_id,
    }
    ctx.record_action(
        action="capture_snapshot", target=camera_id, result="success",
        detail=f"snapshot media #{media_id}",
    )
    return ToolResult(f"Captured snapshot media #{media_id} from {camera_id}.", data)


async def _start_recording(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    camera_id = _str_arg(args, "camera_id")
    await ctx.client.start_recording(camera_id)
    ctx.record_action(action="start_recording", target=camera_id, result="success")
    return ToolResult(f"Recording started on {camera_id}.", {"ok": True, "camera_id": camera_id})


async def _stop_recording(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    camera_id = _str_arg(args, "camera_id")
    result = await ctx.client.stop_recording(camera_id)
    media_id = result.get("media_id")
    ctx.record_action(
        action="stop_recording", target=camera_id, result="success",
        detail=f"recording media #{media_id}",
    )
    return ToolResult(
        f"Recording stopped on {camera_id} (media #{media_id}).",
        {"ok": True, "camera_id": camera_id, "media_id": media_id},
    )


async def _set_motion_detection(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    camera_id = _str_arg(args, "camera_id")
    enabled = bool(args["enabled"])
    cam = await ctx.client.update_camera(camera_id, {"motion_enabled": enabled})
    ctx.record_action(
        action="set_motion_detection", target=camera_id, result="success",
        metadata={"enabled": enabled},
    )
    state = "enabled" if enabled else "disabled"
    return ToolResult(f"Motion detection {state} on {camera_id}.", {"ok": True, "camera": cam})


async def _update_camera_settings(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    camera_id = _str_arg(args, "camera_id")
    body: dict[str, Any] = {}
    for key in ("name", "properties", "transform"):
        if args.get(key) is not None:
            body[key] = args[key]
    if not body:
        raise TailcamMcpError(
            errors.INVALID_REQUEST, "Provide at least one of name, properties, transform."
        )
    cam = await ctx.client.update_camera(camera_id, body)
    ctx.record_action(
        action="update_camera_settings", target=camera_id, result="success",
        metadata={"fields": sorted(body)},
    )
    return ToolResult(f"Updated {sorted(body)} on {camera_id}.", {"ok": True, "camera": cam})


async def _restart_camera(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    camera_id = _str_arg(args, "camera_id")
    _require_confirm(args, ctx.config.require_confirm_for_writes)
    await ctx.client.restart_camera(camera_id)
    ctx.record_action(action="restart_camera", target=camera_id, result="success")
    return ToolResult(f"Restarted camera {camera_id}.", {"ok": True, "camera_id": camera_id})


# --------------------------------------------------------------------------
# node / fleet action tools
# --------------------------------------------------------------------------
async def _reload_node(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    node_key = _str_arg(args, "node_key")
    _require_scope(args, f"reload:{node_key}", ctx.config.require_confirm_for_fleet_writes)
    result = await ctx.client.reload_node(node_key)
    ctx.record_action(
        action="reload_node", target=node_key, result=result.get("result", "success"),
        detail=result.get("detail"),
    )
    return ToolResult(f"Reloaded node {node_key}: {result.get('detail')}", {"ok": True, **result})


async def _reload_fleet_nodes(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    node_keys = args.get("node_keys")
    if not isinstance(node_keys, list) or not node_keys:
        raise TailcamMcpError(errors.INVALID_REQUEST, "node_keys must be a non-empty list.")
    _require_scope(
        args, f"reload:fleet:{len(node_keys)}", ctx.config.require_confirm_for_fleet_writes
    )
    continue_on_error = bool(args.get("continue_on_error", False))
    results: list[dict[str, Any]] = []
    for node_key in node_keys:
        try:
            res = await ctx.client.reload_node(str(node_key))
            results.append({"node_key": node_key, "result": "success", "detail": res.get("detail")})
            ctx.record_action(action="reload_node", target=str(node_key), result="success")
        except TailcamMcpError as exc:
            results.append({"node_key": node_key, "result": "failure", "error": exc.to_payload()})
            ctx.record_action(
                action="reload_node", target=str(node_key), result="failure", detail=exc.message
            )
            if not continue_on_error:
                break
    ok = sum(1 for r in results if r["result"] == "success")
    return ToolResult(
        f"Reloaded {ok}/{len(node_keys)} node(s).",
        {"ok": ok == len(node_keys), "results": results},
    )


async def _check_fleet_version_drift(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    hosts = await ctx.client.hosts()
    try:
        update = await ctx.client.update_info()
    except TailcamMcpError:
        update = {}
    versions = sorted({str(h["version"]) for h in hosts if h.get("version")})
    newest = versions[-1] if versions else None
    latest = update.get("latest") or newest
    drift = [
        {"node_key": h.get("node_key"), "host": h.get("host"), "version": h.get("version")}
        for h in hosts
        if h.get("version") and h.get("version") != latest
    ]
    summary = (
        f"{len(drift)} of {len(hosts)} node(s) behind {latest or '?'}."
        if drift
        else f"All {len(hosts)} node(s) on {latest or '?'}."
    )
    return ToolResult(
        summary,
        {"ok": True, "latest": latest, "update_available": update.get("available", False),
         "drift": drift, "versions": versions},
    )


async def _prepare_fleet_admin_plan(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    goal = _str_arg(args, "goal")
    hosts = await ctx.client.hosts()
    node_filter = args.get("node_filter")
    targets = [
        h for h in hosts
        if not node_filter or node_filter in (h.get("node_key"), h.get("host"))
    ]
    keys = [h.get("node_key") for h in targets]
    steps = [
        {
            "tool": "reload_node",
            "node_key": k,
            "confirm_scope": f"reload:{k}",
            "audited": True,
        }
        for k in keys
    ]
    fleet_step = {
        "tool": "reload_fleet_nodes",
        "node_keys": keys,
        "confirm_scope": f"reload:fleet:{len(keys)}",
        "audited": True,
    }
    summary = (
        f"Plan for goal '{goal}': {len(targets)} target node(s). "
        f"No changes made — supply the listed confirm strings to execute."
    )
    return ToolResult(
        summary,
        {"ok": True, "goal": goal, "targets": keys,
         "per_node_steps": steps, "fleet_step": fleet_step},
    )


# --------------------------------------------------------------------------
# ai / training tools
# --------------------------------------------------------------------------
async def _set_ai_config(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    _require_confirm(args, ctx.config.require_confirm_for_writes)
    body: dict[str, Any] = {}
    for key in ("enabled", "model", "base_url"):
        if args.get(key) is not None:
            body[key] = args[key]
    if not body:
        raise TailcamMcpError(errors.INVALID_REQUEST, "Provide enabled, model, or base_url.")
    ai = await ctx.client.update_ai(body)
    ctx.record_action(
        action="set_ai_config", target="ai", result="success", metadata={"fields": sorted(body)}
    )
    return ToolResult(f"AI config updated ({sorted(body)}).", {"ok": True, "ai": ai})


async def _test_ai_connection(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    ai = await ctx.client.ai()
    summary = (
        f"AI {'reachable' if ai.get('reachable') else 'unreachable'} at {ai.get('base_url')}; "
        f"model {ai.get('model')} {'present' if ai.get('model_present') else 'missing'}."
    )
    return ToolResult(summary, {"ok": True, "ai": ai})


async def _set_training_collection(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    body: dict[str, Any] = {}
    for key in ("enabled", "interval_seconds", "auto_label", "active_dataset_id"):
        if args.get(key) is not None:
            body[key] = args[key]
    if not body:
        raise TailcamMcpError(errors.INVALID_REQUEST, "Provide at least one collection field.")
    training = await ctx.client.update_collection(body)
    ctx.record_action(
        action="set_training_collection", target="training", result="success",
        metadata={"fields": sorted(body)},
    )
    return ToolResult("Training collection updated.", {"ok": True, "training": training})


async def _list_training_datasets(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    datasets = await ctx.client.datasets()
    lines = [
        f"- #{d.get('id')} {d.get('name')} ({d.get('task')}): {d.get('sample_count')} samples"
        for d in datasets
    ]
    summary = f"{len(datasets)} dataset(s):\n" + "\n".join(lines)
    return ToolResult(summary, {"ok": True, "datasets": datasets})


async def _import_events_to_dataset(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    _require_confirm(args, ctx.config.require_confirm_for_writes)
    dataset_id = int(args["dataset_id"])
    dataset = await ctx.client.import_events(dataset_id)
    ctx.record_action(
        action="import_events_to_dataset", target=f"dataset:{dataset_id}", result="success",
        metadata={"sample_count": dataset.get("sample_count")},
    )
    return ToolResult(
        f"Imported events into dataset #{dataset_id} (now {dataset.get('sample_count')} samples).",
        {"ok": True, "dataset": dataset},
    )


# --------------------------------------------------------------------------
# ollama model management
# --------------------------------------------------------------------------
async def _list_ollama_models(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    info = await ctx.client.ollama_models()
    installed = info.get("installed", [])
    reach = "reachable" if info.get("reachable") else "unreachable"
    summary = (
        f"Ollama {reach} at {info.get('base_url')}: {len(installed)} model(s) installed; "
        f"active analyzer model is {info.get('active_model')}."
    )
    return ToolResult(summary, {"ok": True, "ollama": info})


async def _pull_ollama_model(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    _require_confirm(args, ctx.config.require_confirm_for_writes)
    model = _str_arg(args, "model")
    info = await ctx.client.pull_ollama_model(model)
    ctx.record_action(action="pull_ollama_model", target=model, result="success")
    return ToolResult(
        f"Pulled '{model}' into Ollama ({len(info.get('installed', []))} model(s) now installed).",
        {"ok": True, "ollama": info},
    )


async def _load_ollama_model(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    model = _str_arg(args, "model")
    ai = await ctx.client.load_ollama_model(model)
    ctx.record_action(action="load_ollama_model", target=model, result="success")
    return ToolResult(f"Started (warmed) Ollama model '{model}'.", {"ok": True, "ai": ai})


# --------------------------------------------------------------------------
# dataset / sample management
# --------------------------------------------------------------------------
async def _create_dataset(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    name = _str_arg(args, "name")
    body = {"name": name, "note": args.get("note", ""), "task": args.get("task", "classification")}
    ds = await ctx.client.create_dataset(body)
    ctx.record_action(
        action="create_dataset", target=f"dataset:{ds.get('id')}", result="success",
        metadata={"task": body["task"]},
    )
    return ToolResult(
        f"Created dataset #{ds.get('id')} '{ds.get('name')}' ({ds.get('task')}).",
        {"ok": True, "dataset": ds},
    )


async def _delete_dataset(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    _require_confirm(args, ctx.config.require_confirm_for_writes)
    dataset_id = int(args["dataset_id"])
    await ctx.client.delete_dataset(dataset_id)
    ctx.record_action(action="delete_dataset", target=f"dataset:{dataset_id}", result="success")
    return ToolResult(f"Deleted dataset #{dataset_id}.", {"ok": True, "dataset_id": dataset_id})


async def _get_dataset(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    dataset_id = int(args["dataset_id"])
    ds = await ctx.client.dataset(dataset_id)
    summary = (
        f"Dataset #{ds.get('id')} '{ds.get('name')}' ({ds.get('task')}): "
        f"{ds.get('sample_count')} samples, labels {ds.get('label_counts')}."
    )
    return ToolResult(summary, {"ok": True, "dataset": ds})


async def _list_dataset_samples(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    dataset_id = int(args["dataset_id"])
    limit = _clamp(args.get("limit", 50), 1, 500)
    offset = _clamp(args.get("offset", 0), 0, 10**6)
    samples = await ctx.client.dataset_samples(
        dataset_id, label=args.get("label"), limit=limit, offset=offset
    )
    return ToolResult(
        f"{len(samples)} sample(s) in dataset #{dataset_id}.",
        {"ok": True, "samples": samples},
    )


async def _relabel_sample(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    sample_id = int(args["sample_id"])
    label = args.get("label")
    sample = await ctx.client.relabel_sample(sample_id, label)
    ctx.record_action(
        action="relabel_sample", target=f"sample:{sample_id}", result="success",
        metadata={"label": label},
    )
    return ToolResult(
        f"Relabeled sample #{sample_id} to {label!r}.", {"ok": True, "sample": sample}
    )


async def _delete_sample(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    sample_id = int(args["sample_id"])
    await ctx.client.delete_sample(sample_id)
    ctx.record_action(action="delete_sample", target=f"sample:{sample_id}", result="success")
    return ToolResult(f"Deleted sample #{sample_id}.", {"ok": True, "sample_id": sample_id})


# --------------------------------------------------------------------------
# model lifecycle
# --------------------------------------------------------------------------
async def _list_models(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    models = await ctx.client.models()
    lines = [
        f"- #{m.get('id')} {m.get('name')} ({m.get('kind')}/{m.get('task')})"
        f"{' [active]' if m.get('active') else ''}"
        for m in models
    ]
    summary = f"{len(models)} model(s):\n" + "\n".join(lines)
    return ToolResult(summary, {"ok": True, "models": models})


async def _register_model(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    body = {
        "name": _str_arg(args, "name"),
        "path": _str_arg(args, "path"),
        "task": args.get("task", "classification"),
    }
    model = await ctx.client.register_model(body)
    ctx.record_action(
        action="register_model", target=f"model:{model.get('id')}", result="success"
    )
    return ToolResult(
        f"Registered model #{model.get('id')} '{model.get('name')}'.",
        {"ok": True, "model": model},
    )


async def _activate_model(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    model_id = int(args["model_id"])
    model = await ctx.client.activate_model(model_id)
    ctx.record_action(action="activate_model", target=f"model:{model_id}", result="success")
    return ToolResult(
        f"Activated model #{model_id} for motion analysis.", {"ok": True, "model": model}
    )


async def _deactivate_model(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    await ctx.client.deactivate_model()
    ctx.record_action(action="deactivate_model", target="model", result="success")
    return ToolResult("Deactivated trained model; using the default analyzer (Ollama).",
                      {"ok": True})


async def _delete_model(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    _require_confirm(args, ctx.config.require_confirm_for_writes)
    model_id = int(args["model_id"])
    await ctx.client.delete_model(model_id)
    ctx.record_action(action="delete_model", target=f"model:{model_id}", result="success")
    return ToolResult(f"Deleted model #{model_id}.", {"ok": True, "model_id": model_id})


# --------------------------------------------------------------------------
# training runs
# --------------------------------------------------------------------------
async def _start_training_run(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    _require_confirm(args, ctx.config.require_confirm_for_writes)
    body: dict[str, Any] = {"dataset_id": int(args["dataset_id"])}
    for key in ("base_model", "epochs", "image_size"):
        if args.get(key) is not None:
            body[key] = args[key]
    run = await ctx.client.start_run(body)
    ctx.record_action(
        action="start_training_run", target=f"dataset:{body['dataset_id']}", result="success",
        metadata={"run_id": run.get("id"), "base_model": run.get("base_model"),
                  "epochs": run.get("epochs")},
    )
    return ToolResult(
        f"Started training run #{run.get('id')} on dataset #{body['dataset_id']} "
        f"({run.get('base_model')}, {run.get('epochs')} epochs).",
        {"ok": True, "run": run},
    )


async def _list_training_runs(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    runs = await ctx.client.runs()
    lines = [
        f"- run #{r.get('id')} dataset #{r.get('dataset_id')}: {r.get('status')} "
        f"(epoch {r.get('epoch')}/{r.get('epochs')})"
        for r in runs[:15]
    ]
    summary = f"{len(runs)} training run(s):\n" + "\n".join(lines)
    return ToolResult(summary, {"ok": True, "runs": runs})


async def _get_training_run(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    run_id = int(args["run_id"])
    run = await ctx.client.run(run_id)
    summary = (
        f"Run #{run.get('id')}: {run.get('status')} "
        f"(epoch {run.get('epoch')}/{run.get('epochs')}), metrics {run.get('metrics')}."
    )
    return ToolResult(summary, {"ok": True, "run": run})


async def _stop_training_run(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    run_id = int(args["run_id"])
    run = await ctx.client.stop_run(run_id)
    ctx.record_action(action="stop_training_run", target=f"run:{run_id}", result="success")
    return ToolResult(f"Stopped training run #{run_id}.", {"ok": True, "run": run})


# --------------------------------------------------------------------------
# incident / workflow tools
# --------------------------------------------------------------------------
async def _summarize_fleet_health(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    hosts = await ctx.client.hosts()
    node_reports: list[dict[str, Any]] = []
    all_issues: list[dict[str, Any]] = []
    for host in hosts:
        key = host.get("node_key")
        if not host.get("online") and key != "local":
            node_reports.append({"node_key": key, "reachable": False})
            all_issues.append({"node_key": key, "severity": "error", "summary": "node offline"})
            continue
        try:
            health = await ctx.client.node_health(str(key))
        except TailcamMcpError as exc:
            node_reports.append({"node_key": key, "reachable": False, "error": exc.to_payload()})
            continue
        issues = health.get("issues", [])
        for issue in issues:
            all_issues.append({"node_key": key, **issue})
        node_reports.append(
            {
                "node_key": key,
                "reachable": True,
                "version": health.get("version"),
                "cameras_online": health.get("camera_online"),
                "cameras_total": health.get("camera_total"),
                "issues": issues,
            }
        )
    errors_n = sum(1 for i in all_issues if i.get("severity") == "error")
    warns_n = sum(1 for i in all_issues if i.get("severity") == "warning")
    top = "; ".join(f"{i['node_key']}: {i.get('summary')}" for i in all_issues[:5])
    top = top or "all healthy."
    summary = f"{len(hosts)} node(s): {errors_n} error(s), {warns_n} warning(s). {top}"
    return ToolResult(
        summary,
        {"ok": True, "nodes": node_reports, "issues": all_issues,
         "error_count": errors_n, "warning_count": warns_n},
    )


async def _find_offline_cameras(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    cams = await ctx.client.cameras(scope="all")
    bad = [c for c in cams if c.get("status") in ("offline", "degraded")]
    by_node: dict[str, list[dict[str, Any]]] = {}
    for cam in bad:
        host = cam.get("host") or "local"
        by_node.setdefault(host, []).append(
            {
                "id": cam.get("id"),
                "name": cam.get("name"),
                "status": cam.get("status"),
                "likely_cause": cam.get("last_error") or "no recent frames; check device/USB",
            }
        )
    summary = (
        f"{len(bad)} camera(s) offline/degraded across {len(by_node)} node(s)."
        if bad
        else f"All {len(cams)} cameras online."
    )
    return ToolResult(summary, {"ok": True, "offline_by_node": by_node, "count": len(bad)})


async def _investigate_motion_event(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    event_id = int(args["event_id"])
    scope = args.get("scope", "all")
    recent = await ctx.client.events(limit=ctx.config.max_events, scope=scope)
    event = next((e for e in recent if e.get("id") == event_id), None)
    if event is None:
        raise TailcamMcpError(
            errors.INVALID_REQUEST, f"Event #{event_id} not in recent window.",
            status_code=404,
        )
    camera_id = event.get("camera_id")
    nearby = [
        e for e in recent if e.get("camera_id") == camera_id and e.get("id") != event_id
    ][:5]
    prefix = event.get("proxy_prefix", "")
    links: dict[str, Any] = {}
    if event.get("has_thumb"):
        links["thumbnail_url"] = _media_url(prefix, "event", event_id)
    if event.get("recording_id"):
        links["recording_url"] = _media_url(prefix, "media", event.get("recording_id"))
    try:
        camera = await ctx.client.camera(str(camera_id))
    except TailcamMcpError:
        camera = {}
    follow_up = (
        "Review the thumbnail/recording, confirm the AI label, and capture a fresh "
        "snapshot if the camera is still active."
    )
    summary = (
        f"Event #{event_id} on {camera_id}: {event.get('label') or 'motion'} "
        f"(conf {event.get('confidence')}). {len(nearby)} nearby event(s)."
    )
    return ToolResult(
        summary,
        {"ok": True, "event": event, "nearby_events": nearby, "camera": camera,
         "links": links, "suggested_follow_up": follow_up},
    )


async def _prepare_incident_report(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    window_hours = float(args.get("window_hours", 24))
    import time as _time

    since = _time.time() - window_hours * 3600
    events = await ctx.client.events(limit=ctx.config.max_events, scope=args.get("scope", "all"))
    camera_id = args.get("camera_id")
    if camera_id:
        events = [e for e in events if e.get("camera_id") == camera_id]
    windowed = [e for e in events if e.get("start_ts", 0) >= since]
    by_label: dict[str, int] = {}
    for ev in windowed:
        by_label[ev.get("label") or "motion"] = by_label.get(ev.get("label") or "motion", 0) + 1
    lines = [f"# TailCam incident report (last {window_hours:.0f}h)", ""]
    lines.append(f"- Events: {len(windowed)}")
    for label, count in sorted(by_label.items(), key=lambda kv: -kv[1]):
        lines.append(f"  - {label}: {count}")
    lines.append("")
    lines.append("## Notable events")
    notable = sorted(windowed, key=lambda e: -(e.get("confidence") or e.get("peak_score") or 0))
    for ev in notable[:10]:
        lines.append(
            f"- #{ev.get('id')} {ev.get('camera_id')} — {ev.get('label') or 'motion'} "
            f"(conf {ev.get('confidence') or ev.get('peak_score')})"
        )
    markdown = "\n".join(lines)
    return ToolResult(
        f"Incident report covering {len(windowed)} event(s) over {window_hours:.0f}h.",
        {"ok": True, "markdown": markdown, "event_count": len(windowed), "by_label": by_label},
    )


async def _suggest_retention_cleanup(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    system = await ctx.client.system()
    media = await ctx.client.media(limit=ctx.config.max_media, scope=args.get("scope", "all"))
    total_mb = system.get("media_bytes", 0) / (1024 * 1024)
    largest = sorted(media, key=lambda m: -m.get("size_bytes", 0))[:10]
    oldest = sorted(media, key=lambda m: m.get("created_ts", 0))[:10]
    candidates = [
        {"id": m.get("id"), "camera_id": m.get("camera_id"),
         "size_mb": round(m.get("size_bytes", 0) / (1024 * 1024), 1),
         "media_type": m.get("media_type"), "created_ts": m.get("created_ts")}
        for m in largest
    ]
    summary = (
        f"{total_mb:.0f} MB media on disk. Largest item "
        f"{candidates[0]['size_mb'] if candidates else 0} MB. "
        f"This is analysis only — delete via list_recent_media + the web UI."
    )
    return ToolResult(
        summary,
        {"ok": True, "total_media_mb": round(total_mb, 1),
         "largest": candidates,
         "oldest": [{"id": m.get("id"), "created_ts": m.get("created_ts")} for m in oldest]},
    )


# --------------------------------------------------------------------------
# arg helpers
# --------------------------------------------------------------------------
def _str_arg(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TailcamMcpError(errors.INVALID_REQUEST, f"'{key}' is required.")
    return value


def _clamp(value: Any, low: int, high: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, n))


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------
def build_tools() -> list[Tool]:
    cam_id = {"camera_id": {"type": "string", "description": "Camera id."}}
    node_key = {"node_key": {"type": "string", "description": "Node key ('local' or a peer key)."}}
    return [
        # read
        Tool("get_system_status", "System status",
             "Local node version, host, Tailscale state, access URL, and media usage.",
             _NO_INPUT, _get_system_status),
        Tool("list_fleet_nodes", "List fleet nodes",
             "Every known TailCam node with key, host, version, reachability, camera count.",
             _NO_INPUT, _list_fleet_nodes),
        Tool("get_node_health", "Node health",
             "Full v1 health snapshot and issue list for one node.",
             _obj(node_key, ["node_key"]), _get_node_health),
        Tool("list_cameras", "List cameras",
             "Cameras across the fleet (or one node), with status and recording/motion flags.",
             _obj({"scope": {"type": "string", "enum": ["all", "local"], "default": "all"},
                   **node_key}), _list_cameras),
        Tool("inspect_camera", "Inspect camera",
             "One camera's state, geometry, transform, errors, and stream URLs.",
             _obj(cam_id, ["camera_id"]), _inspect_camera),
        Tool("list_recent_events", "Recent motion events",
             "Recent motion events with AI labels, confidence, and thumbnail URLs.",
             _obj({"limit": {"type": "integer", "minimum": 1},
                   "camera_id": {"type": "string"},
                   "scope": {"type": "string", "enum": ["all", "local"], "default": "all"}}),
             _list_recent_events),
        Tool("list_recent_media", "Recent media",
             "Recent snapshots/recordings with size, type, and file/thumbnail URLs.",
             _obj({"limit": {"type": "integer", "minimum": 1},
                   "camera_id": {"type": "string"},
                   "media_type": {"type": "string", "enum": ["snapshot", "recording"]},
                   "scope": {"type": "string", "enum": ["all", "local"], "default": "all"}}),
             _list_recent_media),
        Tool("get_audit_log", "Audit log",
             "Recent management audit events for a node. Admin only.",
             _obj({"limit": {"type": "integer", "minimum": 1, "maximum": 500},
                   "offset": {"type": "integer", "minimum": 0},
                   "node_key": {"type": "string", "default": "local"}}),
             _get_audit_log, min_role=TailCamRole.ADMIN),
        Tool("get_ai_status", "AI status",
             "Ollama/model reachability and training engine/collection state.",
             _NO_INPUT, _get_ai_status),
        # camera actions
        Tool("capture_snapshot", "Capture snapshot",
             "Capture a still from a camera. Returns media id and file URL.",
             _obj(cam_id, ["camera_id"]), _capture_snapshot,
             min_role=TailCamRole.OPERATOR, write=True),
        Tool("start_recording", "Start recording",
             "Start recording a camera.",
             _obj(cam_id, ["camera_id"]), _start_recording,
             min_role=TailCamRole.OPERATOR, write=True),
        Tool("stop_recording", "Stop recording",
             "Stop recording a camera and save the clip.",
             _obj(cam_id, ["camera_id"]), _stop_recording,
             min_role=TailCamRole.OPERATOR, write=True),
        Tool("set_motion_detection", "Set motion detection",
             "Enable or disable motion detection for a camera.",
             _obj({**cam_id, "enabled": {"type": "boolean"}}, ["camera_id", "enabled"]),
             _set_motion_detection, min_role=TailCamRole.OPERATOR, write=True),
        Tool("update_camera_settings", "Update camera settings",
             "Update a camera's display name, capture properties, or transform.",
             _obj({**cam_id,
                   "name": {"type": "string"},
                   "properties": {"type": "object"},
                   "transform": {"type": "object"}}, ["camera_id"]),
             _update_camera_settings, min_role=TailCamRole.OPERATOR, write=True),
        Tool("restart_camera", "Restart camera",
             "Restart a stuck camera feed. Requires confirm=true.",
             _obj({**cam_id, "confirm": {"type": "boolean"}}, ["camera_id"]),
             _restart_camera, min_role=TailCamRole.OPERATOR, write=True),
        # node / fleet
        Tool("reload_node", "Reload node",
             'Reload a node (restart workers, rediscover). Needs confirm_scope "reload:<key>".',
             _obj({**node_key, "confirm_scope": {"type": "string"}}, ["node_key"]),
             _reload_node, min_role=TailCamRole.ADMIN, write=True),
        Tool("reload_fleet_nodes", "Reload fleet nodes",
             'Reload several nodes. Requires confirm_scope="reload:fleet:<count>".',
             _obj({"node_keys": {"type": "array", "items": {"type": "string"}},
                   "confirm_scope": {"type": "string"},
                   "continue_on_error": {"type": "boolean", "default": False}},
                  ["node_keys"]),
             _reload_fleet_nodes, min_role=TailCamRole.ADMIN, write=True),
        Tool("check_fleet_version_drift", "Fleet version drift",
             "Nodes whose version lags the newest/available release.",
             _NO_INPUT, _check_fleet_version_drift),
        Tool("prepare_fleet_admin_plan", "Prepare fleet admin plan",
             "Non-mutating plan with the exact confirm strings needed for a fleet goal.",
             _obj({"goal": {"type": "string"}, "node_filter": {"type": "string"}}, ["goal"]),
             _prepare_fleet_admin_plan),
        # ai / training
        Tool("set_ai_config", "Set AI config",
             "Enable/disable AI analysis or set the model/base_url. Requires confirm=true.",
             _obj({"enabled": {"type": "boolean"}, "model": {"type": "string"},
                   "base_url": {"type": "string"}, "confirm": {"type": "boolean"}}),
             _set_ai_config, min_role=TailCamRole.ADMIN, write=True),
        Tool("test_ai_connection", "Test AI connection",
             "Check AI endpoint reachability and model presence.",
             _NO_INPUT, _test_ai_connection),
        Tool("set_training_collection", "Set training collection",
             "Configure automatic dataset collection from cameras.",
             _obj({"enabled": {"type": "boolean"},
                   "interval_seconds": {"type": "number"},
                   "auto_label": {"type": "boolean"},
                   "active_dataset_id": {"type": "integer"}}),
             _set_training_collection, min_role=TailCamRole.ADMIN, write=True),
        Tool("list_training_datasets", "List datasets",
             "Training datasets with sample counts and task type.",
             _NO_INPUT, _list_training_datasets),
        Tool("import_events_to_dataset", "Import events to dataset",
             "Add recent motion-event snapshots to a dataset. Requires confirm=true.",
             _obj({"dataset_id": {"type": "integer"}, "confirm": {"type": "boolean"}},
                  ["dataset_id"]),
             _import_events_to_dataset, min_role=TailCamRole.ADMIN, write=True),
        # ollama model management
        Tool("list_ollama_models", "List Ollama models",
             "Models installed in the configured Ollama backend, plus reachability.",
             _NO_INPUT, _list_ollama_models),
        Tool("pull_ollama_model", "Pull Ollama model",
             "Download a model into Ollama (can take minutes). Requires confirm=true.",
             _obj({"model": {"type": "string"}, "confirm": {"type": "boolean"}}, ["model"]),
             _pull_ollama_model, min_role=TailCamRole.ADMIN, write=True),
        Tool("load_ollama_model", "Start Ollama model",
             "Warm a model into Ollama's memory ('start' it) for fast first inference.",
             _obj({"model": {"type": "string"}}, ["model"]),
             _load_ollama_model, min_role=TailCamRole.ADMIN, write=True),
        # dataset / sample management
        Tool("create_dataset", "Create dataset",
             "Create a training dataset (classification or detection).",
             _obj({"name": {"type": "string"},
                   "note": {"type": "string"},
                   "task": {"type": "string", "enum": ["classification", "detection"]}},
                  ["name"]),
             _create_dataset, min_role=TailCamRole.ADMIN, write=True),
        Tool("delete_dataset", "Delete dataset",
             "Delete a dataset and its samples. Requires confirm=true.",
             _obj({"dataset_id": {"type": "integer"}, "confirm": {"type": "boolean"}},
                  ["dataset_id"]),
             _delete_dataset, min_role=TailCamRole.ADMIN, write=True),
        Tool("get_dataset", "Get dataset",
             "One dataset's labels, counts, and task type.",
             _obj({"dataset_id": {"type": "integer"}}, ["dataset_id"]), _get_dataset),
        Tool("list_dataset_samples", "List dataset samples",
             "Samples in a dataset, optionally filtered by label.",
             _obj({"dataset_id": {"type": "integer"},
                   "label": {"type": "string"},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                   "offset": {"type": "integer", "minimum": 0}}, ["dataset_id"]),
             _list_dataset_samples),
        Tool("relabel_sample", "Relabel sample",
             "Set or clear a sample's label (pass label=null to clear).",
             {"type": "object",
              "properties": {"sample_id": {"type": "integer"},
                             "label": {"type": ["string", "null"]}},
              "required": ["sample_id"], "additionalProperties": False},
             _relabel_sample, min_role=TailCamRole.ADMIN, write=True),
        Tool("delete_sample", "Delete sample",
             "Delete one sample from its dataset.",
             _obj({"sample_id": {"type": "integer"}}, ["sample_id"]),
             _delete_sample, min_role=TailCamRole.ADMIN, write=True),
        # model lifecycle
        Tool("list_models", "List models",
             "Trained, base, and bring-your-own models with active state.",
             _NO_INPUT, _list_models),
        Tool("register_model", "Register model",
             "Register a bring-your-own model file (.pt) by path.",
             _obj({"name": {"type": "string"}, "path": {"type": "string"},
                   "task": {"type": "string", "enum": ["classification", "detection"]}},
                  ["name", "path"]),
             _register_model, min_role=TailCamRole.ADMIN, write=True),
        Tool("activate_model", "Activate model",
             "Use a trained/BYO model for motion analysis instead of Ollama.",
             _obj({"model_id": {"type": "integer"}}, ["model_id"]),
             _activate_model, min_role=TailCamRole.ADMIN, write=True),
        Tool("deactivate_model", "Deactivate model",
             "Fall back to the default analyzer (Ollama).",
             _NO_INPUT, _deactivate_model, min_role=TailCamRole.ADMIN, write=True),
        Tool("delete_model", "Delete model",
             "Delete a trained/BYO model. Requires confirm=true.",
             _obj({"model_id": {"type": "integer"}, "confirm": {"type": "boolean"}},
                  ["model_id"]),
             _delete_model, min_role=TailCamRole.ADMIN, write=True),
        # training runs
        Tool("start_training_run", "Start training run",
             "Fine-tune a model on a dataset (needs the training engine). confirm=true.",
             _obj({"dataset_id": {"type": "integer"},
                   "base_model": {"type": "string"},
                   "epochs": {"type": "integer", "minimum": 1},
                   "image_size": {"type": "integer", "minimum": 32},
                   "confirm": {"type": "boolean"}}, ["dataset_id"]),
             _start_training_run, min_role=TailCamRole.ADMIN, write=True),
        Tool("list_training_runs", "List training runs",
             "All training runs with status and progress.",
             _NO_INPUT, _list_training_runs),
        Tool("get_training_run", "Get training run",
             "One training run's status, progress, metrics, and log.",
             _obj({"run_id": {"type": "integer"}}, ["run_id"]), _get_training_run),
        Tool("stop_training_run", "Stop training run",
             "Stop a running or queued training run.",
             _obj({"run_id": {"type": "integer"}}, ["run_id"]),
             _stop_training_run, min_role=TailCamRole.ADMIN, write=True),
        # incident / workflow
        Tool("summarize_fleet_health", "Summarize fleet health",
             "Prioritized cross-fleet summary of node health, cameras, and issues.",
             _NO_INPUT, _summarize_fleet_health),
        Tool("find_offline_cameras", "Find offline cameras",
             "Offline/degraded cameras grouped by node with likely causes.",
             _NO_INPUT, _find_offline_cameras),
        Tool("investigate_motion_event", "Investigate motion event",
             "Event detail, nearby events, camera state, media links, and follow-up.",
             _obj({"event_id": {"type": "integer"},
                   "scope": {"type": "string", "enum": ["all", "local"], "default": "all"}},
                  ["event_id"]),
             _investigate_motion_event),
        Tool("prepare_incident_report", "Prepare incident report",
             "Markdown incident summary over a time window for a note, issue, or handoff.",
             _obj({"window_hours": {"type": "number", "default": 24},
                   "camera_id": {"type": "string"},
                   "scope": {"type": "string", "enum": ["all", "local"], "default": "all"}}),
             _prepare_incident_report),
        Tool("suggest_retention_cleanup", "Suggest retention cleanup",
             "Non-mutating analysis of media usage and cleanup candidates.",
             _obj({"scope": {"type": "string", "enum": ["all", "local"], "default": "all"}}),
             _suggest_retention_cleanup),
    ]
