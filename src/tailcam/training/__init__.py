"""On-device model training from your own camera footage.

Collects labeled frames from every camera into datasets, fine-tunes a model
(Ultralytics YOLO classification) on your GPU, and registers the result so it
can be used for camera analysis — or you can plug in your own model. The
training engine is an optional, auto-detected dependency (heavy: torch), so
lean nodes (e.g. a Pi) don't pay for it.
"""
