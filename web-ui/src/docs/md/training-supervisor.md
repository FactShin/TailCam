# Training Supervisor

Approve a finite set of experiments in **Workloads → Training Supervisor**.
Review a dataset's content hash, permitted cameras/classes, registered models,
workers, parameter ranges, seeds, experiment count and resource limits. Add a
metric criterion and evaluation reference. Approval saves a policy; it does
not start training.

The hash includes labels and annotations. If the dataset changes, approve the
new revision before training on it. The existing approval stays immutable.
The worker prepares a fixed subset, and one experiment at a time consumes the
reserved budget. Node-wide admission also applies across other jobs.

An external MCP host can plan the experiments. Give its identity explicit
TailCam **viewer + operator** grants, excluding **admin**. Personal-mode access
without an explicit restricted grant may be administrative. A hidden tool in
the agent's menu is not an authorization boundary.

The initial host tools are `list_training_supervisions`,
`get_training_supervision`, `submit_training_experiment`,
`heartbeat_training_supervisor`, `stop_training_supervision`,
`finish_training_supervision`, `get_training_supervision_report`, and
`list_training_supervision_events`. An administrator can use
`approve_training_supervision`; dataset inspection includes
`get_training_dataset_revision`.

Persist the supervision ID, experiment request and idempotency key, current
job, event cursor and next check in the host's durable state. After reconnecting,
read current state before submitting. Retry a lost request with the same key
and unchanged parameters. A changed request with the same key is rejected.

The agent heartbeat is separate from worker heartbeat and epoch progress.
**Supervisor disconnected** means the external host stopped checking in; the
accepted worker still has its own deadline. MCP does not keep an agent awake.
The host needs a scheduler or persistent process for later checks.

Stop requests prevent new experiments and request worker termination. Continue
polling until stopped or another explicit terminal state appears. Unknown
failures stop the series. Reserved time is not refunded into extra experiments.

The final report lists every run, setting, metric, saved artifact and limitation.
Candidate ranking uses reported training metrics; it is not a validated
deployment gate or a held-out evaluation. This release never activates a model
through supervision. The currently active model remains unchanged.
