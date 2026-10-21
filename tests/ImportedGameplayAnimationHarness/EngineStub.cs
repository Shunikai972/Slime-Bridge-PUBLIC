using System.Reflection;

namespace UnityEngine
{
    public struct Vector3 { }
    [AttributeUsage(AttributeTargets.Field)] public sealed class SerializeField : Attribute { }
    public enum WrapMode { Default, Once, Loop, ClampForever }
    public enum PlayMode { StopSameLayer, StopAll }
    public enum AnimationCullingType { AlwaysAnimate, BasedOnRenderers }
    public enum AnimatorCullingMode { AlwaysAnimate, CullUpdateTransforms, CullCompletely }

    public class Object
    {
        public bool Destroyed;
        public static void Destroy(Object? value)
        {
            if (value == null) return;
            if (value is GameObject root)
            {
                root.DestroyNow();
                return;
            }
            value.Destroyed = true;
            if (value is Component component)
            {
                component.InvokeLifecycle("OnDisable");
                component.InvokeLifecycle("OnDestroy");
                component.gameObject.Remove(component);
            }
        }
        public static bool operator ==(Object? left, Object? right)
        {
            var leftNull = ReferenceEquals(left, null) || (!ReferenceEquals(left, null) && left.Destroyed);
            var rightNull = ReferenceEquals(right, null) || (!ReferenceEquals(right, null) && right.Destroyed);
            return leftNull ? rightNull : !rightNull && ReferenceEquals(left, right);
        }
        public static bool operator !=(Object? left, Object? right) => !(left == right);
        public override bool Equals(object? obj) => ReferenceEquals(this, obj);
        public override int GetHashCode() => base.GetHashCode();
    }

    public class Component : Object
    {
        public GameObject gameObject = null!;
        public Transform transform => gameObject.transform;
        public T? GetComponent<T>() where T : Component => gameObject.GetComponent<T>();
        public Component? GetComponent(Type type) => gameObject.GetComponent(type);
        public T[] GetComponentsInChildren<T>(bool includeInactive = false) where T : Component => gameObject.GetComponentsInChildren<T>(includeInactive);
        public T? GetComponentInChildren<T>(bool includeInactive = false) where T : Component => gameObject.GetComponentInChildren<T>(includeInactive);
        internal void InvokeLifecycle(string method)
        {
            var callback = GetType().GetMethod(method, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            callback?.Invoke(this, null);
        }
    }

    public class MonoBehaviour : Component
    {
        public bool enabled = true;
        public bool isActiveAndEnabled => enabled && gameObject != null && gameObject.activeInHierarchy;
    }

    public sealed class Transform : Component
    {
        private readonly List<Transform> _children = new();
        public Transform? parent { get; private set; }
        public int childCount => _children.Count;
        public Transform GetChild(int index) => _children[index];
        internal void SetParent(Transform? value)
        {
            parent?._children.Remove(this);
            parent = value;
            parent?._children.Add(this);
        }
        public Transform? Find(string path)
        {
            if (string.IsNullOrEmpty(path)) return null;
            var current = this;
            foreach (var part in path.Split('/'))
            {
                Transform? found = null;
                for (var i = 0; i < current.childCount; i++)
                    if (string.Equals(current.GetChild(i).gameObject.name, part, StringComparison.Ordinal)) { found = current.GetChild(i); break; }
                if (found == null) return null;
                current = found;
            }
            return current;
        }
    }

    public class GameObject : Object
    {
        private readonly List<Component> _components = new();
        public readonly Transform transform;
        public string name = "GameObject";
        public int layer;
        public bool activeSelf;
        public bool activeInHierarchy => activeSelf && (transform.parent == null || transform.parent.gameObject.activeInHierarchy);
        public IReadOnlyList<Component> Components => _components;
        public GameObject(string objectName = "GameObject")
        {
            name = objectName;
            transform = new Transform { gameObject = this };
        }
        public T AddComponent<T>() where T : Component
        {
            var component = (T)Activator.CreateInstance(typeof(T), true)!;
            component.gameObject = this;
            _components.Add(component);
            if (activeInHierarchy) component.InvokeLifecycle("OnEnable");
            return component;
        }
        public Component AddComponent(Type type)
        {
            var component = (Component)Activator.CreateInstance(type)!;
            component.gameObject = this;
            _components.Add(component);
            if (activeInHierarchy) component.InvokeLifecycle("OnEnable");
            return component;
        }
        public T? GetComponent<T>() where T : Component
        {
            for (var i = 0; i < _components.Count; i++)
                if (_components[i] is T typed && typed != null) return typed;
            return null;
        }
        public Component? GetComponent(Type type)
        {
            for (var i = 0; i < _components.Count; i++)
                if (type.IsInstanceOfType(_components[i]) && _components[i] != null) return _components[i];
            return null;
        }
        public T[] GetComponentsInChildren<T>(bool includeInactive = false) where T : Component
        {
            var result = new List<T>();
            var stack = new Stack<Transform>();
            stack.Push(transform);
            while (stack.Count > 0)
            {
                var current = stack.Pop();
                if (includeInactive || current.gameObject.activeInHierarchy)
                    for (var i = 0; i < current.gameObject._components.Count; i++)
                        if (current.gameObject._components[i] is T typed && typed != null) result.Add(typed);
                for (var i = current.childCount - 1; i >= 0; i--) stack.Push(current.GetChild(i));
            }
            return result.ToArray();
        }
        public T? GetComponentInChildren<T>(bool includeInactive = false) where T : Component
        {
            var all = GetComponentsInChildren<T>(includeInactive);
            return all.Length == 0 ? null : all[0];
        }
        public void SetActive(bool active)
        {
            if (activeSelf == active) return;
            activeSelf = active;
            InvokeHierarchyLifecycle(active ? "OnEnable" : "OnDisable");
        }
        public void SetParent(Transform parent) => transform.SetParent(parent);
        internal void Remove(Component component) => _components.Remove(component);
        public void Tick()
        {
            if (!activeInHierarchy) return;
            for (var i = 0; i < _components.Count; i++)
                if (_components[i] is MonoBehaviour behaviour && behaviour.isActiveAndEnabled)
                    behaviour.InvokeLifecycle("Update");
            for (var i = 0; i < transform.childCount; i++) transform.GetChild(i).gameObject.Tick();
        }
        private void InvokeHierarchyLifecycle(string method)
        {
            for (var i = 0; i < _components.Count; i++) _components[i].InvokeLifecycle(method);
            for (var i = 0; i < transform.childCount; i++)
                transform.GetChild(i).gameObject.InvokeHierarchyLifecycle(method);
        }
        internal void DestroyNow()
        {
            if (Destroyed) return;
            if (activeSelf) SetActive(false);
            while (transform.childCount > 0)
            {
                var child = transform.GetChild(0).gameObject;
                child.DestroyNow();
                child.transform.SetParent(null);
            }
            for (var i = _components.Count - 1; i >= 0; i--)
            {
                var component = _components[i];
                component.Destroyed = true;
                component.InvokeLifecycle("OnDestroy");
            }
            _components.Clear();
            Destroyed = true;
        }
    }

    public sealed class Animator : MonoBehaviour
    {
        public AnimatorCullingMode cullingMode;
    }

    public sealed class Collider : Component { }
    public struct RaycastHit
    {
        private Collider? _collider;
        public bool ThrowOnColliderRead;
        public Collider? collider { get => ThrowOnColliderRead ? throw new InvalidOperationException("collider getter injected failure") : _collider; set => _collider = value; }
    }

    public sealed class AnimationClip : Object
    {
        public string name = "";
        public bool legacy;
        public float length;
        public WrapMode wrapMode;
    }

    public sealed class AnimationState
    {
        public AnimationClip clip = null!;
        public float time;
        public float speed = 1f;
        public bool enabled;
        public WrapMode wrapMode;
        public AnimationState(AnimationClip value) { clip = value; wrapMode = value.wrapMode; }
    }

    public sealed class Animation : Component
    {
        private readonly Dictionary<string, AnimationState> _states = new(StringComparer.Ordinal);
        public int AddClipCalls;
        public int PlayCalls;
        public bool FailPlay;
        public bool playAutomatically;
        public bool animatePhysics;
        public AnimationCullingType cullingType;
        public AnimationClip? clip;
        public AnimationState? this[string name] => _states.TryGetValue(name, out var state) ? state : null;
        public void AddClip(AnimationClip source, string name) { AddClipCalls++; _states[name] = new AnimationState(source); }
        public bool Play(string name, PlayMode mode)
        {
            PlayCalls++;
            if (FailPlay || !_states.TryGetValue(name, out var selected)) return false;
            foreach (var state in _states.Values) state.enabled = false;
            selected.enabled = true;
            return true;
        }
        public void CrossFade(string name, float length, PlayMode mode) => Play(name, mode);
        public void Stop() { foreach (var state in _states.Values) state.enabled = false; }
    }

    public static class Time { public static float time; }

    public static class Debug
    {
        public static readonly List<string> Messages = new();
        public static void Log(object? value) => Messages.Add(value?.ToString() ?? "");
        public static void LogError(object? value) => Messages.Add(value?.ToString() ?? "");
        public static void LogWarning(object? value) => Messages.Add(value?.ToString() ?? "");
    }
}
