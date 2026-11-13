"""Running the agent on the owner's own ChatGPT / Claude subscription.

The owner signs in on this machine (``sellerclaw-agent connect claude|chatgpt``); the sign-in and
the choice live under ``<data dir>/ai-subscription/`` and never leave the machine. The cloud only
sends which models each role runs on each subscription (``llm.subscription`` in the manifest).
"""

from __future__ import annotations
