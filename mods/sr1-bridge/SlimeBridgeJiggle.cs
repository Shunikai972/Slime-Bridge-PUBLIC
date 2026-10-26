using System;
using System.Collections.Generic;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// Destination-side procedural animation for imported slimes: the source rig deforms through
    /// jiggle/skin bone pairs (bone_jiggle_* animated, bone_skin_* skinned). This component
    /// reproduces the source's procedural behavior in a destination-compatible form: idle
    /// breathing, movement lag/sway on the tail chain, ear wobble and a squash pulse on the core.
    /// The clip data (Idle v4 / Idle_Cotton_Ringtail / ...) is layered on top of this rig in a
    /// later stage; the searches are bounded and cached once at Awake.
    /// </summary>
    internal sealed class SlimeBridgeJiggle : MonoBehaviour
    {
        private Transform? _core;
        private Transform? _top;
        private Transform? _earLeft;
        private Transform? _earRight;
        private readonly List<Transform> _tail = new List<Transform>(6);
        private readonly List<Quaternion> _tailRest = new List<Quaternion>(6);
        private Quaternion _coreRest;
        private Quaternion _topRest;
        private Quaternion _earLeftRest;
        private Quaternion _earRightRest;
        private Vector3 _coreScale;
        private Vector3 _lastWorldPosition;
        private Vector3 _lag;
        private float _phase;

        private void Awake()
        {
            var all = GetComponentsInChildren<Transform>(true);
            for (var i = 0; i < all.Length; i++)
            {
                var t = all[i];
                if (t == null) continue;
                var name = t.gameObject.name;
                if (_core == null && name == "bone_core") _core = t;
                if (_top == null && name == "bone_jiggle_top") _top = t;
                if (_earLeft == null && name == "bone_jiggle_ear_lef") _earLeft = t;
                if (_earRight == null && name == "bone_jiggle_ear_rig") _earRight = t;
                if (name != null && name.StartsWith("bone_jiggle_tail", StringComparison.Ordinal) && _tail.Count < 6)
                {
                    _tail.Add(t);
                    _tailRest.Add(t.localRotation);
                }
            }
            if (_core != null) { _coreRest = _core.localRotation; _coreScale = _core.localScale; }
            if (_top != null) _topRest = _top.localRotation;
            if (_earLeft != null) _earLeftRest = _earLeft.localRotation;
            if (_earRight != null) _earRightRest = _earRight.localRotation;
            _lastWorldPosition = transform.position;
        }

        private void Update()
        {
            var dt = Time.deltaTime;
            if (dt <= 0f) return;
            _phase += dt;
            // Movement lag: the body drags the tail; the actor's own movement supplies the energy.
            var position = transform.position;
            var velocity = (position - _lastWorldPosition) / dt;
            _lastWorldPosition = position;
            var localVelocity = transform.InverseTransformDirection(velocity);
            _lag = Vector3.Lerp(_lag, new Vector3(-localVelocity.x, 0f, -localVelocity.z), Mathf.Clamp01(dt * 4f));

            var breathe = 1f + 0.03f * Mathf.Sin(_phase * 2.1f);
            if (_core != null)
            {
                _core.localScale = new Vector3(_coreScale.x / Mathf.Sqrt(breathe), _coreScale.y * breathe, _coreScale.z / Mathf.Sqrt(breathe));
                _core.localRotation = _coreRest * Quaternion.Euler(2f * Mathf.Sin(_phase * 1.3f), 0f, 2f * Mathf.Cos(_phase * 1.7f));
            }
            if (_top != null)
            {
                _top.localRotation = _topRest * Quaternion.Euler(3.5f * Mathf.Sin(_phase * 2.6f), 2f * Mathf.Sin(_phase * 1.9f), 0f);
            }
            if (_earLeft != null)
            {
                _earLeft.localRotation = _earLeftRest * Quaternion.Euler(0f, 0f, 7f * Mathf.Sin(_phase * 3.1f));
            }
            if (_earRight != null)
            {
                _earRight.localRotation = _earRightRest * Quaternion.Euler(0f, 0f, -7f * Mathf.Sin(_phase * 3.1f + 0.6f));
            }
            for (var i = 0; i < _tail.Count; i++)
            {
                var t = _tail[i];
                if (t == null) continue;
                var amount = 6f + i * 3f;
                var sway = Mathf.Clamp(_lag.z * 25f, -amount, amount);
                var roll = Mathf.Clamp(_lag.x * 30f, -amount, amount);
                var idle = 3f * Mathf.Sin(_phase * 1.6f + i * 0.55f);
                t.localRotation = _tailRest[i] * Quaternion.Euler(roll * 0.4f + idle * 0.5f, sway * 0.35f, idle);
            }
        }
    }
}
