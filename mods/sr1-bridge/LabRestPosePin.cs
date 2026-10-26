using System;
using System.Collections.Generic;
using System.Globalization;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// LAB-only screenshot framing for the rest-pose proof. It never runs in a normal game:
    /// the caller is gated on the exact LAB executable plus SLIMEBRIDGE_REST_POSE=1. The actor is
    /// held at a fixed offset in front of the player camera, upright and facing it (optionally
    /// yawed for a three-quarter view), so a real screenshot is not occluded by the HUD, the
    /// Vacpack or the actor's own back. It adds no visual, material or animation of its own and is
    /// destroyed with the actor.
    /// </summary>
    internal sealed class LabRestPosePin : MonoBehaviour
    {
        internal const string YawVariable = "SLIMEBRIDGE_REST_POSE_YAW";
        private const float Distance = 2.4f;
        private const float LeftOffset = 0.45f;

        private Camera? _camera;
        private Transform? _player;
        private float _yaw;

        internal static bool Attach(GameObject actor, SceneContext scene, List<string> details, List<string> problems)
        {
            if (actor == null || scene == null || scene.Player == null) { problems.Add("rest-pose framing has no actor or player"); return false; }
            var yaw = 0f;
            var raw = Environment.GetEnvironmentVariable(YawVariable);
            if (!string.IsNullOrEmpty(raw) &&
                (!float.TryParse(raw, NumberStyles.Float, CultureInfo.InvariantCulture, out yaw) ||
                 float.IsNaN(yaw) || float.IsInfinity(yaw) || yaw < -180f || yaw > 180f))
            {
                problems.Add("rest-pose framing yaw is not a finite angle in [-180, 180]");
                return false;
            }
            var pin = actor.AddComponent<LabRestPosePin>();
            pin._player = scene.Player.transform;
            pin._yaw = yaw;
            pin._camera = Resolve(pin._player);
            if (pin._camera == null) { UnityEngine.Object.Destroy(pin); problems.Add("rest-pose framing found no active player camera"); return false; }
            details.Add("\"lab_rest_pose_pin\": true");
            details.Add("\"lab_rest_pose_yaw\": " + yaw.ToString("R", CultureInfo.InvariantCulture));
            return true;
        }

        private static Camera? Resolve(Transform? player)
        {
            var main = Camera.main;
            if (main != null && main.isActiveAndEnabled) return main;
            if (player == null) return null;
            foreach (var candidate in player.GetComponentsInChildren<Camera>(false))
                if (candidate != null && candidate.isActiveAndEnabled) return candidate;
            return null;
        }

        private void LateUpdate()
        {
            if (_camera == null || !_camera.isActiveAndEnabled) _camera = Resolve(_player);
            if (_camera == null) return;
            var view = _camera.transform;
            var toCamera = -view.forward;
            toCamera.y = 0f;
            if (toCamera.sqrMagnitude < 0.0001f && _player != null)
            {
                toCamera = -_player.forward;
                toCamera.y = 0f;
            }
            if (toCamera.sqrMagnitude < 0.0001f) return;
            toCamera.Normalize();
            transform.SetPositionAndRotation(
                view.position + view.forward * Distance - view.right * LeftOffset,
                Quaternion.LookRotation(toCamera, Vector3.up) * Quaternion.Euler(0f, _yaw, 0f));
        }
    }
}
