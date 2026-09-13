# Workloads and jobs

Open **Workloads → Placement** to choose different workers for live detection,
motion descriptions, printer analysis, timelapse encoding, interpolation,
labeling and training. A manual choice stays authoritative. Auto considers only
the nodes you explicitly approve, their observed readiness and admission budget.

Register a standalone Ollama provider when a model server is not a TailCam node.
Its provider ID remains separate from TailCam node UUIDs. Registration alone
does not verify its model or hardware. Future speech tasks are shown as reserved.

Each job or live session freezes its placement. Changing settings affects new
work. The **Jobs** tab shows requested and actual target, queue and execution
timing, worker heartbeat, progress, saved outputs and any fallback reason.
A successfully empty detection result differs from unavailable inference.

Long work runs in owned child processes. TailCam reserves queue and worker
capacity, keeps immutable input artifact references, and publishes only the
selected attempt's checked output. Lost replies reconcile the same job and
artifact IDs. A restart does not automatically repeat already-committing work.

**Cancel requested** means the worker is still stopping or its state is being
reconciled. A permitted retry preserves completed earlier stages and stays
within the saved deadline and attempt limits. Supervised training retries also
remain inside the original approval. TailCam never kills the camera server to
stop a training child.

Live detection keeps the running frame and the newest pending frame, so a slow
model does not build a backlog of obsolete images. Received worker assignments
execute locally; they are not routed back to another source.

Configured resource capacity is not a GPU benchmark or a hard filesystem/RAM
quota. Requests requiring unsupported hard limits fail before execution.
Workers that need scratch must permit it under Storage policy; zero-local-media
nodes cannot host that scratch. Missing optional engines are reported as
unavailable. The small built-in detector can provision its supported model
inside the worker's reserved workspace and deadline. Training uses registered
model artifacts. The camera server does not load those engines.

Use **Training Supervisor** for bounded agent-directed experiments. See the
[supervisor guide](/docs/training-supervisor) before granting an agent access.
