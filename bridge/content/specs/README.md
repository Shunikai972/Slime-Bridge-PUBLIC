# Bundled species specs

Base import specs (species SlimeBridge imports into each game): ids, names, diets and palettes only, no game assets.
The launcher copies them into `Lab/GeneratedContent/species-import/<stamp>-bundled/` when the local specs are missing or older
(`bridge/content/spec_seed.py`); Largo entries are added from the caches the player extracted. Ids follow `../species_ids.json`.
