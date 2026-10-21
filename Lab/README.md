# Lab (local only, ignored by Git)

SlimeBridge keeps everything it generates on your PC here, and nothing in this folder is ever committed or released:

* `Launcher/` – launcher settings and caches
* `AssetCache/`, `GeneratedContent/` – what the extractor built from *your* games (species, Largos, island layout)
* `RuntimeArtifacts/`, `Diagnostics/` – logs and diagnostics bundles
* `Games/SR1`, `Games/SR2` – developers only: private copies of the games used to build the plugins and run the LAB
  tests (`py -3.12 -m tools.lab.copy_game <install> Lab/Games/SR1`; the tool never writes to the original install)

Deleting `Lab/` resets SlimeBridge (settings and extracted content); your game saves are not stored here.
