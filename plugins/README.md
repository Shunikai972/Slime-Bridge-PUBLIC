# Prebuilt plugins

Release zips put the compiled plugins here; the launcher installs them into your games from its Games page:

* `SR1/SlimeBridge.SR1.dll` + `modinfo.json` → `<Slime Rancher>/SRML/Mods/SlimeBridge/`
* `SR2/SlimeBridge.SR2.dll` → `<Slime Rancher 2>/Mods/`

In a source checkout this folder is empty: build the plugins with `py -3.12 build_all.py --plugins-only` (see README).
