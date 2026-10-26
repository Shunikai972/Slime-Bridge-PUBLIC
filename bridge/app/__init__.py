"""Application services behind the SlimeBridge launcher (no Qt, no native APIs).

Each service composes lower layers (counterpart, deploy, transition, multiplayer, packages,
modcompat) into launcher-shaped read models and *plans*. Destructive or native actions are never
executed from here directly: services return plans that a caller confirms and an executor or local
adapter performs. UI code (``apps/launcher``) depends on this package, never the reverse.
"""
