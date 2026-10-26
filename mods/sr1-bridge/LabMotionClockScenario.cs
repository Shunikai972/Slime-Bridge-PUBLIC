using System;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>Only attached by the process/path-guarded motion observer in the LAB.
    /// Advances the native game clock, never an actor transform, force or petrification flag.</summary>
    internal sealed class LabMotionClockScenario : MonoBehaviour
    {
        private ImportedPetrification? _machine;
        private float _since;
        private int _phase;
        private float _deadline;
        private bool _testImpulse;
        internal bool ExternalImpulseApplied { get; private set; }

        internal static void Attach(GameObject actor)
        {
            if (Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_MOTION_SCENARIO") != "day-night") return;
            var scenario = actor.AddComponent<LabMotionClockScenario>();
            scenario._machine = actor.GetComponent<ImportedPetrification>();
            scenario._since = Time.realtimeSinceStartup;
            scenario._deadline = scenario._since + 240f;
            scenario._testImpulse = Environment.GetEnvironmentVariable("SLIMEBRIDGE_LAB_MOTION_IMPULSE") == "1";
        }

        private void Update()
        {
            if (_phase >= 4 || _machine == null) return;
            var now = Time.realtimeSinceStartup;
            if (now > _deadline) { _phase = 4; return; }
            try
            {
                var clock = SceneContext.Instance?.TimeDirector;
                if (clock == null) return;
                if (_phase == 0 && now - _since >= 25f)
                {
                    clock.FastForwardTo(clock.GetNextDawn());
                    _phase = 1;
                    UnityEngine.Debug.Log("[SlimeBridge] motion scenario requested native dawn");
                }
                else if (_phase == 1 && _machine.IsPetrified)
                {
                    _phase = 2;
                    _since = now;
                }
                else if (_phase == 2 && _testImpulse && !ExternalImpulseApplied && now - _since >= 20f)
                {
                    var body = GetComponent<Rigidbody>();
                    if (body != null && !body.isKinematic)
                    {
                        // Explicitly labelled external intervention, never an autonomy fix.
                        body.AddForce(Vector3.right * body.mass, ForceMode.Impulse);
                        ExternalImpulseApplied = true;
                        UnityEngine.Debug.Log("[SlimeBridge] motion scenario applied labelled external impulse");
                    }
                }
                else if (_phase == 2 && now - _since >= 30f)
                {
                    clock.FastForwardTo(clock.HoursFromNow(12f));
                    _phase = 3;
                    UnityEngine.Debug.Log("[SlimeBridge] motion scenario requested native night");
                }
                else if (_phase == 3 && !_machine.IsPetrified) _phase = 4;
            }
            catch (Exception error)
            {
                _phase = 4;
                UnityEngine.Debug.LogWarning("[SlimeBridge] motion clock scenario stopped: " + error.GetType().Name);
            }
        }
    }
}
