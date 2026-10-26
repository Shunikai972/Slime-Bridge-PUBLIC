using System;
using System.Collections;
using System.Linq;
using System.Text;
using HarmonyLib;
using SlimeBridge.Shared;
using UnityEngine;

namespace SlimeBridge.SR1
{
    /// <summary>
    /// The "H" of the sanctuary: a clone of the game's own Hobson journal device (the glowing yellow H beacon on its orange pod,
    /// "Press E to activate"), whose entry shows the sanctuary's message instead of one of Hobson's journals. Fail-open: when the
    /// native device cannot be found the builder falls back to its stone tablet.
    /// </summary>
    internal static class SanctuaryJournal
    {
        internal const string EntryKey = "slimebridge.sanctuary.h";
        private static string _text = "";
        private static Harmony? _harmony;

        internal static string Compose(SanctuaryTablet spec)
        {
            // consecutive lines are one paragraph, a blank line starts the next
            var sb = new StringBuilder();
            sb.Append("<size=160%><b>").Append(spec.Title).Append("</b></size>\n\n");
            var paragraph = new StringBuilder();
            void Flush() { if (paragraph.Length > 0) { sb.Append(paragraph).Append("\n\n"); paragraph.Clear(); } }
            foreach (var line in spec.Lines)
            {
                if (line.Length == 0) { Flush(); continue; }
                if (paragraph.Length > 0) paragraph.Append(' ');
                paragraph.Append(line);
            }
            Flush();
            sb.Append("<i>").Append(spec.Signature).Append("</i>");
            return sb.ToString();
        }

        internal static void EnsurePatched(string text)
        {
            _text = text;
            if (_harmony != null) return;
            try
            {
                var harmony = new Harmony("slimebridge.sr1.sanctuary-journal");
                harmony.Patch(AccessTools.Method(typeof(JournalUI), "SetJournalKey"),
                    postfix: new HarmonyMethod(typeof(SanctuaryJournal), nameof(SetJournalKeyPostfix)));
                _harmony = harmony;
            }
            catch (Exception ex) { UnityEngine.Debug.LogError("[SlimeBridge] sanctuary journal patch failed: " + ex.GetType().Name); }
        }

        private static void SetJournalKeyPostfix(JournalUI __instance, string journalKey)
        {
            try
            {
                if (journalKey == EntryKey && __instance.journalText != null) __instance.journalText.text = _text;
            }
            catch (Exception) { /* leaves the game's own (empty) text */ }
        }

        /// <summary>Clones a native journal device to <paramref name="position"/>; null when none exists in this build/scene.</summary>
        internal static GameObject? Create(Transform parent, Vector3 position, float yaw, SanctuaryTablet spec, out string note)
        {
            note = "";
            JournalEntry? source = null;
            var best = -1;
            foreach (var entry in Resources.FindObjectsOfTypeAll<JournalEntry>())
            {
                if (entry == null || entry.uiPrefab == null) continue;
                var root = entry.transform;
                var score = 0;
                if (root.GetComponentsInChildren<Transform>(true).Any(t => t.name.StartsWith("HobsHologram", StringComparison.Ordinal))) score += 2;
                if (root.GetComponentsInChildren<Transform>(true).Any(t => t.name.IndexOf("FXPool HobsHologram", StringComparison.Ordinal) >= 0)) score += 1;
                if (score > best) { best = score; source = entry; }
            }
            if (source == null) { note = "no native journal device in this scene"; return null; }

            // the node that owns the device (nodeJournal) carries the whole model; clone that, not just the entry component
            var node = source.transform;
            while (node.parent != null && !node.name.StartsWith("nodeJournal", StringComparison.Ordinal)) node = node.parent;
            if (!node.name.StartsWith("nodeJournal", StringComparison.Ordinal)) node = source.transform;

            EnsurePatched(Compose(spec));
            var clone = UnityEngine.Object.Instantiate(node.gameObject);
            clone.name = spec.Id + ".journal";
            clone.transform.SetParent(parent, true);
            foreach (var entry in clone.GetComponentsInChildren<JournalEntry>(true)) entry.entryKey = EntryKey;

            var ground = position;
            if (Physics.Raycast(position + Vector3.up * 3f, Vector3.down, out var hit, 8f, ~0, QueryTriggerInteraction.Ignore)) ground = hit.point;
            clone.transform.position = ground;
            clone.transform.rotation = Quaternion.Euler(0f, yaw, 0f);
            clone.SetActive(true);
            var keeper = new GameObject(clone.name + ".keeper").AddComponent<SanctuaryJournalKeepActive>();
            keeper.transform.SetParent(parent, false);
            keeper.Target = clone;
            note = "native journal device cloned from " + node.name;
            return clone;
        }
    }

    /// <summary>JournalEntry.Start hides the device in story-suppressing game modes; the sanctuary's message must stay reachable.</summary>
    internal sealed class SanctuaryJournalKeepActive : MonoBehaviour
    {
        internal GameObject? Target;

        private IEnumerator Start()
        {
            yield return null;
            yield return null;
            if (Target != null && !Target.activeSelf) Target.SetActive(true);
        }
    }
}
