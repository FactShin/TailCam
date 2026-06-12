"""AI analysis of motion events via a local Ollama vision model.

Cheap pixel-motion gates this, so the model is only asked about a frame or two
per event. Everything degrades gracefully: if Ollama is unreachable, slow, or
returns junk, analysis returns None and the event stays a plain motion event.
"""

from __future__ import annotations
