"""Procedural 3D content for SlimeBridge (stdlib only; original geometry, no game assets).

``portal`` builds the SlimeBridge gateway (visual LODs, collision proxy, trigger volume, sockets,
metadata) from validated ``PortalParams``; ``export_gltf`` / ``export_obj`` / ``sidecar`` write
GLB, OBJ+MTL and the ``.portal.json`` sidecar; ``preview`` renders PNG previews for humans;
``cli`` writes everything under the git-ignored ``Lab/GeneratedContent``. The Blender helper in
``blender/portal_blender.py`` is a standalone script and is never imported by product code.

Evidence: geometry, contracts and GLB structure are UNIT_TESTED here; how the asset looks or
behaves in Blender, Unity or a game is NEEDS_LOCAL_VISUAL_PROOF.
"""
