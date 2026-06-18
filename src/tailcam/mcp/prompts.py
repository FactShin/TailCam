"""MCP prompts: reusable TailCam workflows for clients that expose them.

Each prompt returns a single user message that frames a task and points the agent
at the right tools/resources. Prompts are static templates with optional
arguments interpolated into the text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Prompt:
    name: str
    description: str
    arguments: list[dict[str, Any]] = field(default_factory=list)
    template: str = ""


def _arg(name: str, description: str, required: bool = False) -> dict[str, Any]:
    return {"name": name, "description": description, "required": required}


PROMPTS = [
    Prompt(
        "tailcam_fleet_triage",
        "Guide a fleet health review and safe next actions.",
        template=(
            "Review the TailCam fleet. Start with the `tailcam://fleet` resource and the "
            "`summarize_fleet_health` tool, then `find_offline_cameras` and "
            "`check_fleet_version_drift`. Report errors first, then warnings. Propose next "
            "actions but do NOT change state without explaining the confirm string each "
            "action needs."
        ),
    ),
    Prompt(
        "tailcam_motion_investigation",
        "Investigate a motion event and suggest next steps.",
        arguments=[_arg("event_id", "Motion event id to investigate.", required=True)],
        template=(
            "Investigate TailCam motion event #{event_id}. Use `investigate_motion_event`, "
            "examine the thumbnail/recording links and the camera's current state, summarize "
            "the evidence, and recommend follow-up. Capture a fresh snapshot only if useful."
        ),
    ),
    Prompt(
        "tailcam_camera_tuning",
        "Tune a camera's motion, resolution, FPS, transform, and recording for a goal.",
        arguments=[
            _arg("camera_id", "Camera to tune.", required=True),
            _arg("goal", "What the user wants (e.g. 'reduce false motion at night')."),
        ],
        template=(
            "Tune TailCam camera {camera_id} for this goal: {goal}. Inspect it with "
            "`inspect_camera`, then propose changes via `update_camera_settings` and "
            "`set_motion_detection`. Explain trade-offs (bandwidth, storage, false positives) "
            "before applying anything."
        ),
    ),
    Prompt(
        "tailcam_tailscale_debug",
        "Diagnose Tailscale Serve, app capabilities, access URLs, and peer discovery.",
        template=(
            "Diagnose TailCam's Tailscale integration. Read `tailcam://system` and each node's "
            "health, check tailscale_installed/running/served and access URLs, and confirm peer "
            "discovery via `list_fleet_nodes`. Explain any unreachable peers and how app "
            "capabilities affect remote roles."
        ),
    ),
    Prompt(
        "tailcam_ai_setup",
        "Configure local Ollama/model analysis and explain fleet analyzer choices.",
        template=(
            "Help configure TailCam AI analysis. Use `get_ai_status` and `test_ai_connection`, "
            "then guide model/base_url choices with `set_ai_config` (confirm=true). Explain "
            "when one tailnet host should analyze the whole fleet's events."
        ),
    ),
    Prompt(
        "tailcam_admin_change_plan",
        "Draft a safe change plan before any fleetwide action.",
        arguments=[_arg("goal", "The administrative goal.", required=True)],
        template=(
            "Draft a safe TailCam change plan for: {goal}. Use `prepare_fleet_admin_plan` to "
            "enumerate target nodes and the exact confirm strings. Present the plan for "
            "approval; execute only after the operator confirms scope."
        ),
    ),
]

_BY_NAME = {p.name: p for p in PROMPTS}


def render(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Return MCP ``prompts/get`` payload, or raise KeyError if unknown."""

    prompt = _BY_NAME[name]
    args = {a["name"]: "" for a in prompt.arguments}
    args.update({k: str(v) for k, v in (arguments or {}).items()})
    try:
        text = prompt.template.format(**args)
    except (KeyError, IndexError):
        text = prompt.template
    return {
        "description": prompt.description,
        "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
    }
