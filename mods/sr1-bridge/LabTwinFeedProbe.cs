using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    // Only created by the exact-LAB imported-op gate and the explicit twin_largofy op.
    // No direct eating/transformation, hunger or definition mutations. Native AI decides whether to eat.
    internal sealed class LabTwinFeedProbe
    {
        private readonly GameObject _original;
        private readonly GameObject _food;
        private readonly SlimeEat _eater;
        private readonly SlimeEat.OnEatDelegate _onEat;
        private readonly LabTwinChompTrace _chompTrace;
        private readonly string _outDir, _runId;
        private readonly Identifiable.Id _target;
        private readonly Vector3 _origin;
        private readonly HashSet<int> _beforeIds;
        private readonly float _deadline;
        private readonly bool _canLargofy, _liveDefinitionMatches;
        private readonly int _eatEntries;
        private bool _atePink, _done;
        private readonly List<string> _samples = new List<string>();
        private float _nextSample;
        private bool _projectileDelivered;
        private readonly float _deliverAt = Time.realtimeSinceStartup + 5f;
        private readonly string _deliveryMode = Environment.GetEnvironmentVariable("SLIMEBRIDGE_TWIN_PLORT_SHOT") ?? "";
        private readonly bool _restDelivery;

        private string ReadState()
        {
            if (_original == null || _eater == null) return "{\"actor_alive\":false}";
            var map = _eater.GetAllEats();
            var hasDrive = map != null && map.TryGetValue(Identifiable.Id.PINK_PLORT, out _);
            var distance = _food != null ? Vector3.Distance(_original.transform.position, _food.transform.position) : -1f;
            var seekers = _original.GetComponents<FindConsumable>();
            var emotions = _original.GetComponent<SlimeEmotions>();
            var chomper = _original.GetComponent<Chomper>();
            return "{\"actor_alive\":true,\"pink_in_live_all_eats\":" + Bool(hasDrive) +
                ",\"eater_enabled\":" + Bool(_eater.enabled) + ",\"food_edible\":" + Bool(_food != null && Identifiable.IsEdible(_food)) +
                ",\"food_distance\":" + distance.ToString("R", CultureInfo.InvariantCulture) +
                ",\"min_drive\":" + (hasDrive ? map[Identifiable.Id.PINK_PLORT].minDrive : -1f).ToString("R", CultureInfo.InvariantCulture) +
                ",\"projectile_delivered\":" + Bool(_projectileDelivered) +
                ",\"can_chomp\":" + Bool(chomper != null && chomper.CanChomp()) + ",\"is_chomping\":" + Bool(chomper != null && chomper.IsChomping()) +
                ",\"fixed_time\":" + Time.fixedTime.ToString("R", CultureInfo.InvariantCulture) +
                ",\"animators\":[" + string.Join(",", _original.GetComponentsInChildren<Animator>(true).Select(anim => "{\"enabled\":" + Bool(anim.enabled) + ",\"culling\":" + RegistrySnapshotFormat.Quote(anim.cullingMode.ToString()) + "}")) + "]" +
                ",\"agitation\":" + (emotions != null ? emotions.GetCurr(SlimeEmotions.Emotion.AGITATION) : -1f).ToString("R", CultureInfo.InvariantCulture) +
                ",\"seekers\":[" + string.Join(",", seekers.Select(s => "{\"type\":" + RegistrySnapshotFormat.Quote(s.GetType().Name) + ",\"enabled\":" + Bool(s.enabled) + "}")) + "]}";
        }

        private LabTwinFeedProbe(GameObject original, GameObject food, SlimeEat eater,
            Identifiable.Id target, string outDir, string runId, SlimeDefinition definition)
        {
            _original = original; _food = food; _eater = eater; _target = target;
            _restDelivery = _deliveryMode == "rest";
            _outDir = outDir; _runId = runId; _origin = original.transform.position;
            _beforeIds = new HashSet<int>(UnityEngine.Object.FindObjectsOfType<Identifiable>().Select(i => i.GetInstanceID()));
            _deadline = Time.realtimeSinceStartup + 45f;
            _canLargofy = definition.CanLargofy;
            _liveDefinitionMatches = eater.slimeDefinition == definition;
            _eatEntries = eater.slimeDefinition?.Diet?.EatMap?.Count ?? 0;
            _chompTrace = LabTwinChompTrace.Start(eater, food);
            _onEat = id => { if (id == Identifiable.Id.PINK_PLORT) _atePink = true; };
            eater.onEat += _onEat;
        }

        internal static LabTwinFeedProbe? TryStart(GameContext game, SceneContext scene,
            Identifiable.Id twinId, string outDir, string runId, List<string> details, List<string> problems)
        {
            var twins = UnityEngine.Object.FindObjectsOfType<Identifiable>().Where(i => i != null && i.id == twinId).ToArray();
            if (twins.Length != 1) { problems.Add("Twin feeding probe requires exactly one Twin actor"); return null; }
            var original = twins[0].gameObject;
            var eater = original.GetComponent<SlimeEat>();
            var definition = game.SlimeDefinitions.GetSlimeByIdentifiableId(twinId);
            var prefab = game.LookupDirector.GetPrefab(Identifiable.Id.PINK_PLORT);
            if (eater == null || definition == null || prefab == null)
            { problems.Add("Twin feeding probe missing native eater/definition/PinkPlort"); return null; }
            var target = Identifiable.Id.NONE;
            foreach (var entry in eater.slimeDefinition?.Diet?.EatMap ?? new List<SlimeDiet.EatMapEntry>())
                if (entry.eats == Identifiable.Id.PINK_PLORT && entry.becomesId != Identifiable.Id.NONE) target = entry.becomesId;
            // Deliberately allow a missing target to produce a diagnostic failure, never fabricate a target or fix maps here.
            var position = original.transform.position + original.transform.forward * 0.75f + Vector3.up * 0.1f;
            var food = UnityEngine.Object.Instantiate(prefab, position, Quaternion.identity);
            food.SetActive(true);
            scene.GameModel.RegisterStartingActor(food, MonomiPark.SlimeRancher.Regions.RegionRegistry.RegionSetId.HOME);
            var probe = new LabTwinFeedProbe(original, food, eater, target, outDir, runId, definition);
            details.Add("\"natural_feed_target\": " + ((int)target).ToString(CultureInfo.InvariantCulture));
            details.Add("\"natural_feed_actor\": " + original.GetInstanceID().ToString(CultureInfo.InvariantCulture));
            details.Add("\"natural_feed_food\": " + food.GetInstanceID().ToString(CultureInfo.InvariantCulture));
            return probe;
        }

        internal void Tick()
        {
            // Explicit LAB setup after the native edible delay: deliver only the created plort.
            // Rest mode avoids fast departing food cancelling a normal bite. Neither mode pins
            // food repeatedly, invokes eating, or changes the actor or its hunger/definition.
            if (!_done && !_projectileDelivered && Time.realtimeSinceStartup >= _deliverAt &&
                (_deliveryMode == "1" || _restDelivery) && _original != null && _food != null)
            {
                _projectileDelivered = true;
                var body = _food.GetComponent<Rigidbody>();
                if (body != null)
                {
                    var direction = _original.transform.forward;
                    body.position = _original.transform.position + direction * (_restDelivery ? .7f : 1.6f) + Vector3.up * 0.2f;
                    body.velocity = _restDelivery ? Vector3.zero : -direction * 5f;
                }
            }
            if (!_done && Time.realtimeSinceStartup >= _nextSample && _samples.Count < 10)
            {
                _nextSample = Time.realtimeSinceStartup + 5f;
                try { _samples.Add(ReadState()); }
                catch (Exception error) { _samples.Add("{\"read_error\":" + RegistrySnapshotFormat.Quote(error.GetType().Name) + "}"); }
            }
            // Native EatAndTransform instantiates synchronously before emitting onEat. Inspect
            // the resulting actor on the following Tick, not only 40 seconds after the meal.
            // Otherwise a moving largo can leave this observation radius before the first scan.
            var mealCompleted = _atePink && _food == null && _original == null;
            if (_done || (!mealCompleted && Time.realtimeSinceStartup < _deadline)) return;
            var rows = new List<string>();
            var newTargets = 0;
            foreach (var actor in UnityEngine.Object.FindObjectsOfType<Identifiable>())
            {
                if (actor == null || _beforeIds.Contains(actor.GetInstanceID())) continue;
                var distance = Vector3.Distance(actor.transform.position, _origin);
                if (distance > 20f) continue;
                if (actor.id == _target && _target != Identifiable.Id.NONE) newTargets++;
                rows.Add("{\"id\":" + ((int)actor.id).ToString(CultureInfo.InvariantCulture) +
                    ",\"instance\":" + actor.GetInstanceID().ToString(CultureInfo.InvariantCulture) +
                    ",\"distance\":" + distance.ToString("F3", CultureInfo.InvariantCulture) + "}");
            }
            var accepted = TwinLargofyEvidence.Accept(_target != Identifiable.Id.NONE, _atePink,
                _food == null, _original == null, newTargets);
            if (!accepted && Time.realtimeSinceStartup < _deadline) return;
            _done = true;
            Stop();
            var payload = "{\"schema\":\"slimebridge-natural-twin-feed\",\"run_id\":" + RegistrySnapshotFormat.Quote(_runId) +
                ",\"passed\":" + Bool(accepted) + ",\"can_largofy\":" + Bool(_canLargofy) +
                ",\"live_eater_definition_matches\":" + Bool(_liveDefinitionMatches) + ",\"eat_entries\":" + _eatEntries +
                ",\"expected_largo\":" + ((int)_target).ToString(CultureInfo.InvariantCulture) +
                ",\"pink_plort_eat_event\":" + Bool(_atePink) + ",\"food_gone\":" + Bool(_food == null) +
                ",\"original_gone\":" + Bool(_original == null) + ",\"new_target_count\":" + newTargets +
                ",\"delivery_mode\":" + RegistrySnapshotFormat.Quote(_restDelivery ? "rest" : (_deliveryMode == "1" ? "shot" : "none")) +
                ",\"new_nearby_actors\":[" + string.Join(",", rows) + "],\"chomp_trace\":" + _chompTrace.ToJson() +
                ",\"read_only_samples\":[" + string.Join(",", _samples) + "]}";
            RegistrySnapshotFormat.PublishCaptureFile(_outDir, "visual-diagnostics-natural-twin-feed.json", payload);
            Debug.Log("[SlimeBridge] natural Twin feeding passed=" + accepted + " eatEvent=" + _atePink + " target=" + (int)_target);
        }

        internal void Stop()
        {
            try { _chompTrace.Stop(); }
            finally
            {
                // Unity may have destroyed the original actor after a successful native transform.
                try { if (_eater != null) _eater.onEat -= _onEat; }
                catch (MissingReferenceException) { }
            }
        }
        private static string Bool(bool value) => value ? "true" : "false";
    }
}
