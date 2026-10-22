# Releasing SlimeBridge

A release is one zip on GitHub Releases: the source (without tests), the two prebuilt plugins and the bundled extractor.
Players never need a .NET SDK or the game assemblies.

## Checklist

1. All tests green: `tools\run_tests.cmd` (no game running).
2. Bump `VERSION` **and** `"version"` in `mods/sr1-bridge/modinfo.json` (same `X.Y.Z`; the script refuses a mismatch).
3. Move the `## [X.Y.Z] - unreleased` section of `CHANGELOG.md` to today's date; its text becomes the release notes.
4. Commit: `git commit -am "Release vX.Y.Z"`.
5. Build the zip:

   ```
   py -3.12 -m tools.release.make_release --sr1-ref "<SR1 folder with SRML>" --sr2-ref "<SR2 folder with MelonLoader>"
   ```

   It copies the tracked files, builds `SlimeBridge.SR1.dll` / `SlimeBridge.SR2.dll` into `plugins/SR1` and `plugins/SR2`,
   **refuses the release if any game file, extracted asset, model, image or audio file was staged**, then writes
   `dist/SlimeBridge-vX.Y.Z-win64.zip`, `dist/SHA256SUMS.txt` and `dist/release-notes-vX.Y.Z.md`.
6. Test the zip like a player: extract it to a new folder, run `Install-SlimeBridge.cmd`, start `SlimeBridge.cmd`, install
   the plugins into a game and play once.
7. Publish (push + GitHub release through the API with the credential git already stores):

   ```
   py -3.12 -m tools.release.make_release --sr1-ref ... --sr2-ref ... --publish        # add --draft to review first
   ```

   or by hand: `git tag vX.Y.Z && git push origin vX.Y.Z`, then create the release on GitHub and attach the zip and
   `SHA256SUMS.txt`.

## Continuous integration

`.github/workflows/ci.yml` runs the Python test suite on Windows for every push and pull request. It cannot build the
plugins (the game assemblies are not public), so releases are always built locally with the script above.
