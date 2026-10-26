# SR2 focus component findings — October 7, 2026

Status: **BLOCKED_UNDER_STRICT_FOCUS_CONTRACT**. The refusing component is identified in the existing-save diagnostic, but no permissible correction of the native focus state is established. Intro advancement and gameplay-control restoration remain unverified. Do not weaken the Unity-and-Win32 guard or repeat a fenced native CREATE.

## Runtime observation (already acquired; not repeated)

Artifact: `Lab/RuntimeArtifacts/focus-20261007-124637-900776`.

- The authorized existing-save run produced 115 own-SR2-PID rows over 29.965 seconds.
- Unity returned `Application.isFocused=true` before and after every row; platform was `WindowsPlayer:2`, and `runInBackground=true`.
- User32 returned the same nonzero foreground HWND and foreign PID in every row; no capture exception or Win32 error was recorded. The separate external samples agreed on the foreground PID.
- Telemetry, cleanup, original/LAB fingerprints, and save restoration passed. This does not prove intro advancement, controls, or the historical NEWGAME run's internal focus state.
- No CREATE, item grants, input, window activation or focus-state patch was performed.

## Exact interop independently checked

Parent re-hashed and decompiled the actual LAB `MelonLoader/Il2CppAssemblies/UnityEngine.CoreModule.dll` with ILSpy after receiving the read-only investigator's report. File: 3,275,776 bytes, SHA-256 `7d3a7ab4f8a7195e6bd25740287238e9ae4ec53241766bf8d3ca418d53665adb`.

The generated `UnityEngine.Application` focus getter has a 36-byte CIL body: it invokes the resolved IL2CPP method, raises any reported exception, unboxes its result as `System.Boolean`, and returns it. There is no custom HWND test or managed focus heuristic in this wrapper. This makes an obvious managed conversion error unlikely; it does **not** verify the native engine implementation or the runtime method-pointer target.

The generated static initializer resolves distinct original IL2CPP metadata tokens:

| Getter | Decimal target token | Correct hexadecimal token |
| --- | --- | --- |
| `get_isFocused` | `100663569` | `0x06000111` |
| `get_runInBackground` | `100663571` | `0x06000113` |
| `get_platform` | `100663586` | `0x06000122` |

These are resolver target tokens, not native machine-code RVAs. The investigator's original report printed incorrect hex equivalents (`0x06000011`, `0x06000013`, `0x06000022`); parent verified the actual initializer and calculated the corrections above. The original report is retained unchanged under active-profile scratch, SHA-256 `4a82288b7f0b0918c743388757b62ac4ec6bd6cb6cf354dd4c420de964be9161`.

Parent-generated decompilation evidence remains local in `sr2-focus-diag-20261007/focus-interop-parent.txt` and `focus-interop-parent.il`; no decompiled game code or binary is included in this repository. The project-owned SR2 Harmony calls inspected target the new-game iterator and save-file naming, not the Unity focus getter.

## Primary-source scope and remaining hypotheses

Sources retrieved on October 7, 2026:

1. Unity Issue Tracker [UUM-44773](https://issuetracker.unity.com/issues/1723), “Application.isFocused is showing Unity window still focused when unfocusing at Play Mode start.” The issue is explicitly categorized **Editor Platform**, with Windows 11 and Unity 2021.3.26f1 among the reproduced versions. Its resolution discusses startup `WM_ACTIVATE` and links the Microsoft article below. It is not evidence of a defect or applicable fix in SR2's standalone Unity 2021.3.28f1 player.
2. Raymond Chen, Microsoft, [“Why does my window get a WM_ACTIVATE message when it isn't active?”](https://devblogs.microsoft.com/oldnewthing/20131016-00/?p=2913/) (October 16, 2013). The article distinguishes per-thread/input-group active and focused windows from the system foreground window: a window created after a slow startup can legitimately remain active for its own thread while another application's window is foreground.

The Microsoft source supplies a plausible **semantic distinction**, not merely a missing-message theory. The present capture cannot distinguish that normal thread-local activation state from stale Unity state or a player-specific defect: it contains no own-thread GUI activation snapshot, activation-message history or Unity focus-transition trace. The exact native Unity mapping to those states remains unknown.

`runInBackground` and `isFocused` have distinct generated bindings. The former explains continued ticking, not the focus getter's value. Nothing in the inspected wrapper justifies replacing the focused result with an OS-only decision.

## Boundary and next permissible discriminator

Keep `BackgroundGameFocus.IsBackground()` unchanged: Unity must report unfocused, the foreground HWND/PID must be stable and nonzero, and its PID must differ from this game. Unknown or conflicting observations refuse automation.

If further diagnosis is needed, collect passive, timestamp-correlated Unity focus transitions and own-thread GUI activation state alongside foreground HWND/PID in an explicitly authorized existing-save session. Do not send activation messages, attach input queues, activate windows or manufacture `isFocused=false`. No additional runtime or probe implementation is authorized by this findings document. Until that evidence supplies a lawful correction, retain the intro task as blocked and advance independent inventory/content work.
