"""Source gloss must flow from the SR2 material into the native body-material clone.

The destination native `SR/AMP/Slime/Body/Stripe` shader exposes `_Gloss` and
`_GlossPower`; a rebind that keeps the TEMPLATE species' values renders imported
slimes with the template's shine (Tabby _Gloss 1.2) instead of the source's matte
balance (Ringtail `_GlossRGB` mean ~0.35), which produced the over-bright
highlight look on the imported Ringtail.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_gloss_plumbing_is_present_in_loader_marker_and_rebind():
    marker = (ROOT / 'mods/sr1-bridge/ImportedPartColors.cs').read_text(encoding='utf-8-sig')
    assert 'public float Gloss' in marker and 'public float GlossPower' in marker
    loader = (ROOT / 'mods/sr1-bridge/ImportedModelLoader.cs').read_text(encoding='utf-8-sig')
    assert '"_GlossRGB"' in loader and '"_GlossPowerRGB"' in loader
    assert 'partData.Gloss = gloss' in loader and 'partData.GlossPower = glossPower' in loader
    spawner = (ROOT / 'mods/sr1-bridge/ImportedSpawner.cs').read_text(encoding='utf-8-sig')
    assert 'SetMaterialFloat(clone, "_Gloss", partData.Gloss)' in spawner
    assert 'SetMaterialFloat(clone, "_GlossPower", partData.GlossPower)' in spawner
    # NaN sentinel = source declares nothing; the template value must stay untouched.
    assert 'float.IsNaN(partData.Gloss)' in spawner and 'float.IsNaN(partData.GlossPower)' in spawner
