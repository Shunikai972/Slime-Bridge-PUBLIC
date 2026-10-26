"""Hybrid Bridge multiplayer: protocol, transport-agnostic server core, persistence, directory.

Two distinct realms (SR1, SR2) never share a physical world simulation. What players share is the
Bridge layer: identity and sessions, presence and realm membership, chat, player lists, server
announcements, content manifests, profile association and transition intents. Native realm sync
(actors, inventories, world state) belongs to local realm adapters and is out of scope here.
"""
