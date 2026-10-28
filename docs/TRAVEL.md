# Travelling between Slime Rancher and Slime Rancher 2

Travel is **passing from one game to the other**, not merging them. Both games stay open and in sync; the player goes through a
portal in one, a Slime-Rancher-style transition plays, the other game comes to the front on the matching save, and the game that was
left drops to the minimum until the player comes back.

## Why two processes (single-process was evaluated)

Putting both games in one process was investigated first, because it would be the nicest result. It is not possible:

| | Slime Rancher 1 | Slime Rancher 2 |
|---|---|---|
| Unity player | 2019.4.29 (`UnityPlayer.dll`) | 2021.3.28 (`UnityPlayer.dll`) |
| Scripting | Mono (`SlimeRancher_Data/Managed/*.dll`) | IL2CPP (`GameAssembly.dll`) + MelonLoader |
| Mod loader | SRML / Harmony 1 | MelonLoader / Il2CppInterop |

A process can host one Unity player: two different `UnityPlayer.dll` builds export the same entry points, own the same window, GPU
device, audio device, input hook and static state, and cannot be loaded side by side. The two script runtimes (Mono and IL2CPP) also
cannot share an object model, and the scene/asset formats differ between the Unity versions. There is no supported or practical
way to "load SR1 as a scene of SR2". The design below gets the same experience with two cooperating processes.

## Flow

```
SR1 portal ──portal_cross──▶ BridgeHost ──▶ launcher TravelCoordinator
                                                │ prepare_departure ─▶ SR1   : iris closes, "Travelling to Slime Rancher 2"
                                                │ (starts SR2 on the linked save if it is not running, hidden behind SR1)
                                                │ arrive            ─▶ SR2   : window to the front, iris opens, player emerges
                                                │ hold              ─▶ SR1   : body parked at its portal, few FPS, lowest quality,
                                                ▼                              muted, cameras off, simulation frozen (single player)
                                             COMPLETE                          window minimized
```

Return trip is the same flow with the roles swapped: the held game gets `arrive` (throttle undone, player placed in front of its
portal facing away, light flash, iris opens: the player *comes out of the portal*).

* States of a crossing: `PREPARING → SWITCHING → HOLDING → COMPLETE`, or `ROLLED_BACK` / `FAILED`. Each step has a deadline; on any
  failure the source is told to `resume` (its overlay opens, input and quality come back) and its window is restored and focused.
* The coordinator runs on a launcher thread every 0.3 s, whether the launcher window is shown or not (the window's own timers are
  slowed to about once a minute when it is hidden behind a game, which used to freeze the crossing: "nothing happens").
* A crossing that cannot start or fails tells the player in the game why (`resume` with a reason -> message at the top of the screen);
  walking into the portal shows *"The portal opens…"* at once, a sleeping portal shows how many statues are filled.
* Messages are at-least-once through a per-realm mailbox in BridgeHost (`/v1/travel/send|next|event|events`). A message stays at the
  head until its event is acknowledged; `resume`/`abort` jump the queue so a stuck instruction can never block the way back.
* Safety nets in the plugin: a held game resumes by itself if BridgeHost stays silent for 45 s, or if the player returns to its
  window by hand and stays for 4 s.
* Multiplayer (SR1 side): the traveller's body stays in the running SR1 world, parked in front of the portal with health kept full;
  with the `THROTTLE` policy the world keeps simulating slowly instead of freezing. Background policy is a launcher option.

## Saves: one ranch, one other-game save

A save of one game is **linked** to one save of the other game (`travel-links.json`, next to the launcher settings). The launcher
never creates, edits or deletes a save; it only remembers the pairing.

* First crossing with a save that has no link (`travel_link_mode = AUTO`, the default): nothing is asked. If the other game is
  open on a save that belongs to no ranch yet, that save is linked; otherwise the other game is started and creates a **new save**
  for this ranch by itself (SR1: `LoadNewGame`; SR2 1.x: `AutoSaveDirector.StartNewGame` with the New Game screen's default
  settings in the first free slot, see `mods/sr2-bridge/Sr2NewGame.cs`), and the link is recorded as soon as it reports its name.
  `travel_link_mode = ASK` keeps the old dialog (*"Which Slime Rancher 2 save belongs to this ranch?"*).
* Linked: the other game is started on exactly that save (`SLIMEBRIDGE_TRAVEL_MODE=DESTINATION` + `SLIMEBRIDGE_TRAVEL_SAVE`, existing saves
  only), loads it by itself and waits behind the portal card until the player arrives.
* The other game is already running on a **different** save than the linked one: the crossing is refused (`DESTINATION_WRONG_SAVE`);
  a running game is never switched behind the player's back. A link whose save no longer exists is dropped and the question is asked again.
* **New save** (ASK mode): when SR1 is the destination, is not running and the player has no save to link, the dialog also offers *"Create a new Slime Rancher save for this ranch"*. The launcher starts SR1 with `SLIMEBRIDGE_TRAVEL_NEW=<name>`; the plugin creates it with the game's own `LoadNewGame` (never an existing save) and the link is recorded when the new save reports its name. Proven in the protected LAB run `travel_oracle --scenario create` (SR2 save -> new SR1 save, hens carried). In AUTO mode SR2 creates its new save itself (above).
* `travel_save_mode = CONTINUE` skips linking and uses the other game's latest save (old behaviour).

## What you carry

At the moment the portal animation has closed, the source game reports what is in the vac (`departure_ready` carries the list); the
destination adds what exists in its game and fits (items matched by a canonical name: `CARROT_VEGGIE` = `IdentifiableType.Carrot`,
`PINK_PLORT` = `IdentifiableType.PinkPlort`, `PINK_SLIME` = `SlimeDefinition.Pink`) and reports what it really accepted; only that is then
removed from the source. Items are **moved, never copied**; what does not exist over there or does not fit stays in your vac.
Setting `travel_carry`: `ALL` (default), `SLIMES_PLORTS`, `NONE`. Proven with both real games in both directions (carrots, plorts, hens).
Currencies are never carried.

## Settings (launcher → Bridge options)

| key | values | meaning |
|---|---|---|
| `travel_save_mode` | `LINKED`, `CONTINUE` | which save the other game uses |
| `travel_carry` | `ALL`, `SLIMES_PLORTS`, `NONE` | what travels with you |
| `sr1_portal_requires_statues` | bool | off: the SR1 portal opens as soon as the walkway does |
| `sr2_portal_enabled` / `sr2_portal_gate_enabled` / `sr2_portal_cost` | bool / bool / int | the SR2 return portal, its price (default 250 000 newbucks, paid once per save) |
| `background_policy` | `FREEZE`, `THROTTLE` | what the game you leave does while you are away |

## Code map

* Launcher: `apps/launcher/travel.py` (coordinator + links), `apps/launcher/windows.py` (focus/minimize/restore, only for verified game PIDs)
* Host: `bridge/host/server.py` travel mailbox, `/v1/portal/cross` and `/v1/portal/requests`
* Plugins (shared): `mods/shared/TravelAgent.cs` (mailbox client + at-least-once logic), `TravelOverlay.cs` (iris + waiting card),
  `TravelThrottle.cs` (FPS/quality/audio/cameras/time scale)
* SR1: `mods/sr1-bridge/Sr1Travel.cs`, portal hooks in `SanctuaryHost.cs`; SR2: `mods/sr2-bridge/Sr2Travel.cs`
* Evidence: `py -3.12 -m tools.lab.travel_oracle --layout <layout> --current-user-authorized` (both real games, protected run)
* SR2 return portal: `mods/sr2-bridge/Sr2ReturnPortal.cs` (pad of the game's own labyrinth-portal bricks + column of light, placed once per save
  on flat free ground near where the player lives, anchor and "paid" flag in `sr2-portal-progress.json`; arrival puts the player in front of it)
* Items: `mods/shared/TravelCarry.cs`, `mods/sr1-bridge/Sr1Carry.cs`, `mods/sr2-bridge/Sr2Carry.cs`
* Tests: `tests/sanctuary/test_host_travel.py`, `tests/test_launcher_travel.py`, `tests/SanctuaryHarness` (agent logic)
