"""Production-linked SR1 behavior at a synthetic native-engine boundary, not gameplay proof."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DOTNET = shutil.which("dotnet")

class SR1BackgroundIntroTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DOTNET:
            raise unittest.SkipTest("dotnet SDK required")
        scratch = Path(os.environ["TMPDIR"])
        scratch.mkdir(parents=True, exist_ok=True)
        cls.dotnet = DOTNET
        cls.temp = tempfile.TemporaryDirectory(prefix="sr1-intro-test-", dir=scratch)
        cls.env = os.environ.copy()
        for key in ("TEMP", "TMP", "TMPDIR", "DOTNET_CLI_HOME"):
            cls.env[key] = cls.temp.name
        cls.env.update(DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1", DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_GENERATE_ASPNET_CERTIFICATE="false")
        cls.project = ROOT / "tests/SR1BackgroundIntroHarness/SR1BackgroundIntroHarness.csproj"
        cls.out = Path(cls.temp.name) / "build"
        result = subprocess.run([DOTNET, "build", str(cls.project), "-o", str(cls.out), "--nologo"], cwd=ROOT, env=cls.env, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise AssertionError("harness build failed (not behavioral RED):\n" + result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_case(self, mode):
        result = subprocess.run([self.dotnet, str(self.out / "SR1BackgroundIntroHarness.dll"), mode], cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("PASS " + mode, result.stdout)

    def test_background_newgame_finishes_original_native_sequence(self):
        self.run_case("happy")

    def test_guards_fail_closed_and_never_rearm(self):
        for mode in (
            "foreground", "unknown-focus", "loading", "tween-stack", "name", "name-foreground", "director", "save-object", "context",
            "root-scene", "unloaded-scene", "menu", "special", "prefab", "canvas", "disabled", "inactive",
            "duplicate-sequence", "multicast-sequence", "wrong-sequence-method", "tweener-not-sequence", "null-lists",
            "loaded-save", "callback-error", "timeout", "tick-budget", "unload", "unload-before-arm", "focus-race",
            "identity-race", "callback", "multicast-monitor", "monitor-context", "monitor-director", "child-owner",
            "bad-template", "double-monitor", "unknown-contract",
        ):
            with self.subTest(mode=mode):
                self.run_case(mode)

    def test_native_ownership_provenance_and_lifecycle_boundaries(self):
        for mode in ("unauthorized", "no-journal", "prior-intent", "replay", "existing", "begin-throw", "begin-error", "reconcile",
                     "intent-order", "pending-name", "paused", "other-pause", "callback-throw", "retired", "natural-finish", "unknown-update", "focus-contract"):
            with self.subTest(mode=mode):
                self.run_case(mode)

    def test_only_success_retains_owner_beyond_creation_result(self):
        self.run_case("result-success")
        self.run_case("result-uncertain")

if __name__ == "__main__":
    unittest.main()
