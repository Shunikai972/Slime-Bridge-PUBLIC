using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using Il2CppInterop.Runtime;
using Il2CppMonomiPark.SlimeRancher;
using Il2CppMonomiPark.SlimeRancher.Persist;
using SlimeBridge.Shared;

namespace SlimeBridge.SR2
{
    // Main-thread, detached full-payload readback. CREATE/readiness remains CounterpartCreator-owned.
    internal sealed class Sr2InventoryPersistence
    {
        private readonly Il2Cpp.GameContext _game;
        private readonly Il2Cpp.SceneContext _scene;
        private readonly Il2Cpp.AutoSaveDirector _director;
        private readonly Il2Cpp.FileStorageProvider _provider;
        private readonly IntPtr _gamePtr, _scenePtr, _directorPtr, _savedPtr, _providerPtr, _ammoPtr, _lookupPtr, _playerPtr;
        private readonly string _identity, _root;
        private readonly Il2Cpp.IdentifiableType _type;
        private readonly Dictionary<string, string> _before;

        internal Sr2InventoryPersistence(Il2Cpp.GameContext game, Il2Cpp.SceneContext scene, string identity, Il2Cpp.IdentifiableType type)
        {
            _game = game; _scene = scene; _identity = identity; _type = type;
            _director = game.AutoSaveDirector ?? throw new InvalidOperationException("native director unavailable");
            var storage = _director.StorageProvider ?? throw new InvalidOperationException("native storage unavailable");
            if (storage.ObjectClass != Il2CppClassPointerStore<Il2Cpp.FileStorageProvider>.NativeClassPtr)
                throw new InvalidOperationException("native file storage class required");
            _provider = storage.TryCast<Il2Cpp.FileStorageProvider>() ?? throw new InvalidOperationException("native file storage cast failed");
            _root = Path.GetFullPath(_provider.savePath);
            if (!Path.IsPathRooted(_provider.savePath) || !_provider.IsInitialized())
                throw new InvalidOperationException("native file storage root not initialized");
            _gamePtr = game.Pointer; _scenePtr = scene.Pointer; _directorPtr = _director.Pointer;
            _savedPtr = _director.SavedGame?.Pointer ?? IntPtr.Zero; _providerPtr = _provider.Pointer;
            _ammoPtr = scene.PlayerState!.Ammo!.Pointer; _lookupPtr = game.LookupDirector!.Pointer; _playerPtr = scene.Player!.Pointer;
            if (!Current()) throw new InvalidOperationException("loaded inventory/storage changed");
            _before = Fingerprints(Names()); // Reject ambiguous/unreadable storage before INTENT/native effects.
            if (!Current()) throw new InvalidOperationException("loaded inventory/storage changed during capture");
        }

        internal SaveProof Save(int expectedCount)
        {
            var proof = InventoryRules.VerifyPersistence(() =>
            {
                if (!Current()) return false;
                // Preflight precedes INTENT; repeat after the inventory effect, immediately before save.
                // Pathname checks reject existing reparse points, not concurrent path-swap atomicity.
                foreach (var name in Names()) NativePath(name);
                return Current() && _director.SaveAllNow();
            }, () =>
            {
                var after = Names();
                string? revision = null;
                foreach (var pair in Fingerprints(after))
                    if (!_before.TryGetValue(pair.Key, out var oldHash) || oldHash != pair.Value)
                    {
                        if (revision != null) throw new InvalidDataException("multiple native revisions changed");
                        revision = pair.Key;
                    }
                if (revision == null || Latest(after) != revision)
                    throw new InvalidDataException("no unique newly written latest native revision");
                return Read(revision);
            }, _identity, expectedCount);
            return Finish(proof, expectedCount);
        }

        internal SaveProof Recheck(CommandResult receipt)
        {
            var proof = InventoryRules.VerifyPersistence(() => Current(), () =>
            {
                if (Latest(Names()) != receipt.SaveFile) throw new InvalidDataException("receipt no longer names current native revision");
                return Read(receipt.SaveFile);
            }, _identity, receipt.After);
            if (proof.Verified && !string.Equals(proof.Sha256, receipt.Sha256, StringComparison.Ordinal))
                return new SaveProof(false, "", "", "receipt native bytes changed");
            return Finish(proof, receipt.After);
        }

        private SaveProof Finish(SaveProof proof, int expectedCount)
        {
            try
            {
                if (proof.Verified && (!Current() || LiveCount() != expectedCount || Latest(Names()) != proof.SaveFile))
                    return new SaveProof(false, "", "", "loaded inventory/storage changed during native readback");
                return proof;
            }
            catch (Exception ex) { return new SaveProof(false, "", "", "native final recheck failed: " + ex.GetType().Name); }
        }

        private bool Current()
        {
            if (!CounterpartCreator.TryReadWorld(out var world) || world == null || world.Name != _identity ||
                world.Game.Pointer != _gamePtr || world.Scene.Pointer != _scenePtr || world.Director.Pointer != _directorPtr ||
                _director.SavedGame?.Pointer != _savedPtr || _scene.Player?.Pointer != _playerPtr ||
                _scene.PlayerState?.Ammo?.Pointer != _ammoPtr || _game.LookupDirector?.Pointer != _lookupPtr) return false;
            var storage = _director.StorageProvider;
            return storage != null && storage.Pointer == _providerPtr &&
                storage.ObjectClass == Il2CppClassPointerStore<Il2Cpp.FileStorageProvider>.NativeClassPtr &&
                _provider.IsInitialized() && Path.IsPathRooted(_provider.savePath) &&
                string.Equals(Path.GetFullPath(_provider.savePath), _root, StringComparison.Ordinal);
        }

        private List<string> Names()
        {
            if (!Current()) throw new InvalidOperationException("native storage changed");
            RejectReparsePath(_root); // Even an empty revision list must not bypass the root/ancestor preflight.
            if ((File.GetAttributes(_root) & FileAttributes.Directory) == 0)
                throw new InvalidDataException("native storage root is not a directory");
            var names = new List<string>(); var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var name in _provider.GetAvailableGames())
                if (InventoryRules.IsRevision(name, _identity))
                {
                    if (!seen.Add(name)) throw new InvalidDataException("duplicate native revision");
                    names.Add(name);
                }
            return names;
        }

        // Native rotation can overwrite an existing suffix. Physical fingerprints select
        // the changed candidate only; full native parsing + byte/disk comparison prove it.
        private Dictionary<string, string> Fingerprints(List<string> names)
        {
            var result = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach (var name in names)
                using (var sha = SHA256.Create())
                using (var stream = File.OpenRead(NativePath(name)))
                    result.Add(name, BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant());
            return result;
        }

        private string NativePath(string revision)
        {
            var path = _provider.GetFullFilePath(revision);
            if (!Path.IsPathRooted(path) || !string.Equals(Path.GetDirectoryName(Path.GetFullPath(path)), _root, StringComparison.Ordinal) ||
                Path.GetFileNameWithoutExtension(path) != revision || Path.GetExtension(path) != ".sav" || !Current())
                throw new InvalidDataException("native exact file path/world changed");
            RejectReparsePath(path);
            if ((File.GetAttributes(path) & FileAttributes.Directory) != 0)
                throw new InvalidDataException("native revision is not a regular file");
            return path;
        }

        private static void RejectReparsePath(string path)
        {
            // Follow no save stream until every existing component, including the root, is plain.
            // Missing/unreadable components also throw and fail closed. No native no-follow API is assumed.
            for (var component = Path.GetFullPath(path); !string.IsNullOrEmpty(component); component = Path.GetDirectoryName(component))
                if ((File.GetAttributes(component) & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidDataException("reparse point in native storage path");
        }

        // Raw suffixes are recycled, not ordered. Use exact native summary timestamps only
        // to select a unique latest raw file; summaries never establish inventory/count proof.
        private string Latest(List<string> names)
        {
            string? latest = null; long timestamp = long.MinValue; var tied = false;
            foreach (var name in names)
            {
                var summary = _director.LoadSummary(name);
                if (summary == null || summary.IsInvalid || summary.Name != _identity || summary.SaveName != name)
                    throw new InvalidDataException("native revision summary identity unavailable");
                var ticks = summary.SaveTimestamp.Ticks;
                if (ticks > timestamp) { latest = name; timestamp = ticks; tied = false; }
                else if (ticks == timestamp) tied = true;
            }
            if (latest == null || tied || !Current()) throw new InvalidDataException("ambiguous latest native revision");
            return latest;
        }

        private SaveReadback Read(string revision)
        {
            if (!Current() || !InventoryRules.IsRevision(revision, _identity)) throw new InvalidDataException("native revision/world mismatch");
            var native = new Il2CppSystem.IO.MemoryStream(); byte[] bytes;
            try
            {
                _provider.GetGameData(revision, native);
                bytes = native.ToArray(); // Native provider may leave Position at EOF.
            }
            finally { native.Dispose(true); }
            if (bytes.Length == 0) throw new InvalidDataException("empty native payload");
            var payload = new Il2CppSystem.IO.MemoryStream(bytes, false); var game = new GameV06();
            try
            {
                game.Load(payload, false); // Full detached load, including the native payload terminator.
                if (payload.Position != payload.Length || game._isLoadSummary || game.Player == null ||
                    game.World == null || game.Ranch == null || game.Actors == null || game.IdentTypeIndex?.IndexTable == null)
                    throw new InvalidDataException("incomplete native game payload");
                var lookup = _game.LookupDirector!._identifiableTypeByRefId?.Cast<Il2CppSystem.Collections.Generic.IReadOnlyDictionary<string, Il2Cpp.IdentifiableType>>();
                if (lookup == null) throw new InvalidDataException("native identifiable reverse lookup unavailable");
                var ids = new IdentifiableTypePersistenceIdReverseLookupTable(game.IdentTypeIndex.IndexTable, lookup);
                var ammo = game.Player.Ammo;
                if (ammo == null) throw new InvalidDataException("native saved ammo unavailable");
                var live = _scene.PlayerState!.Ammo!;
                for (var i = 0; i < live.Slots.Length; i++)
                    if (live.Slots[i]?.IsUnlocked == true && i >= ammo.Count) throw new InvalidDataException("native saved ammo slot missing");
                var count = 0;
                for (var i = 0; i < ammo.Count; i++)
                {
                    var entry = ammo[i];
                    if (entry == null || entry.Count < 0) throw new InvalidDataException("invalid native saved ammo entry");
                    if (entry.Count == 0) continue;
                    var type = ids.GetIdentifiableType(entry.ID);
                    if (type == null || string.IsNullOrEmpty(type.ReferenceId)) throw new InvalidDataException("unresolved native saved ammo type");
                    // Saved ammo is a complete payload, not a projection of today's usable live slots.
                    // Include every resolved positive target entry so the exact expected total fails closed.
                    if (type.ReferenceId == _type.ReferenceId)
                        count = checked(count + entry.Count);
                }
                var path = NativePath(revision);
                return new SaveReadback(revision, game.GameName, count, bytes, path);
            }
            finally { payload.Dispose(true); }
        }

        private int LiveCount()
        {
            var slots = _scene.PlayerState!.Ammo!.Slots; var count = 0;
            for (var i = 0; i < slots.Length; i++)
            {
                var slot = slots[i];
                if (slot?.Id?.Pointer == _type.Pointer && slot.Count > 0)
                    count = checked(count + slot.Count);
            }
            return count;
        }
    }
}
