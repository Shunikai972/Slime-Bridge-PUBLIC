# Third-party notices

Upstream projects used as references (commits read on October 3, 2026). None of their code is copied into SlimeBridge source. SlimeBridge plugins only reference loader assemblies at build time (`<Private>false</Private>`); they are not redistributed — players install SRML / MelonLoader themselves.

| Project | Commit | License (from the repo's license file) | Role |
|---|---|---|---|
| SlimeRancherModding/SRML | `11440f26938f` | GPL-3.0 | SR1 loader (the lab copy ships SRML 0.2.1.0) |
| cachenetworks/srmp-public | `a97a9c9d0216` | GPL-3.0 | SR1 multiplayer reference |
| ZervoZadachin/srmp-public | `8cf361fe1d71` | GPL-3.0 | SR1 multiplayer historical reference |
| LavaGang/MelonLoader | `5aff4d9d7c16` | Apache-2.0 | SR2 loader (release v0.7.3 installed in the SR2 lab) |
| ThatFinn/Starlight | `78197b71eb82` | Starlight Source-Available License (Starlight-SAL), not an OSI license | SR2 mod menu/API reference (latest release v4.0.4) |
| SlimeRancherModding/MelonSRML | `5bb5bff782fc` | GPL-3.0 | SR2 content API reference |
| pyeight/SlimeRancher2Multiplayer | `619bc4cf871a` | GPL-3.0 | SR2 multiplayer (Ranching Together); latest release Beta_0.3.9 |

Downloaded binaries in the lab (not committed): `MelonLoader.x64.zip` v0.7.3, SHA-256 `5b2b2f3d1cd42b59ec886c5bdc2663edae87a0097a4f4a8f58c0965a99dda416`, matching the GitHub release asset digest.

## Launcher interface (bundled, in `apps/launcher/ui/fonts/`)

| Font | License | Role |
|---|---|---|
| Fredoka (Fredoka Project Authors) | SIL Open Font License 1.1 (`OFL-Fredoka.txt`) | titles and buttons |
| Nunito (Nunito Project Authors) | SIL Open Font License 1.1 (`OFL-Nunito.txt`) | body text |

The header logo, the offline slimes/plorts and the landscape in the launcher are drawn in code (`ui/js/art.js`, `ui/js/scene.js`) for this project.

News, key art and slime pictures shown in the launcher are **Monomi Park, LLC** materials, loaded at run time from slimerancher.com / monomipark.com (and their CDN) and cached locally under the ignored `Lab/Launcher/cache/`; none is bundled or committed. They are shown under Monomi Park's Fan Content Policy (https://www.slimerancher.com/fan-content-policy): SlimeBridge is unofficial Fan Content, not approved/endorsed by Monomi Park. Portions of the materials used are property of Monomi Park, LLC. ©Monomi Park LLC.

## SRMP Revival (bundled in release zips: `plugins/SRMP/`)

The release zip carries an unmodified build of SRMP Revival (a maintained fork of SRMP, Slime Rancher multiplayer) so the
launcher can install it for online play. It is a separate program under the **GNU GPL v3** (its license travels with it as
`plugins/SRMP/LICENSE-GPL-3.0.txt`); its complete corresponding source is published at
https://github.com/Shunikai972/Slime-rancher-multiplayer-public-mode. SlimeBridge does not link to it; the two mods share a
game process only.

## Python packages (installed from PyPI by `Install-SlimeBridge.cmd`, not bundled)

| Package | License | Role |
|---|---|---|
| pywebview | BSD-3-Clause | launcher window |
| UnityPy, TypeTreeGeneratorAPI | MIT | reading the player's own game files |
| dnfile | MIT | reading .NET metadata of the player's own game |
| numpy, Pillow, pygltflib, texture2ddecoder, PyYAML | BSD / MIT-CMU / MIT / MIT / MIT | maths, images, glTF, texture decoding, YAML |

AssetRipper (GPL-3.0) is optional for the extractor: it is never bundled or linked, only started as a separate program if the player installed it and set `SBCONV_ASSETRIPPER`.
