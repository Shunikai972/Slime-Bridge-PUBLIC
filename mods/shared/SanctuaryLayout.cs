using System;
using System.Collections.Generic;

namespace SlimeBridge.Shared
{
    /// <summary>
    /// Loader-neutral reader for <c>bridge-sanctuary-layout</c> v1 (written by <c>bridge/sanctuary/layout.py</c>).
    /// Strict and fail-closed: any missing or mistyped field throws <see cref="FormatException"/>, so a hand-edited or
    /// stale layout can never half-build an island. Only names, transforms and parameters live in it; meshes and
    /// materials are resolved from the running game by name.
    /// </summary>
    public sealed class SanctuaryLayout
    {
        public string KitSha256 = "", RootName = "";
        public string AccessMode = "ENDGAME";
        public int GoldCost = 10;
        public bool DynamicRequirements;
        public float DeckTopY;
        public float[] FrameOrigin = new float[3];
        public float HeadingY;
        public List<SanctuaryPiece> Pieces = new List<SanctuaryPiece>();
        public List<SanctuaryStatue> Statues = new List<SanctuaryStatue>();
        public SanctuaryGate Gate = new SanctuaryGate();
        public SanctuaryPortal Portal = new SanctuaryPortal();
        public SanctuarySpawn Spawn = new SanctuarySpawn();
        public List<string> RequiredSlimes = new List<string>();
        public List<SanctuaryShot> Shots = new List<SanctuaryShot>();

        public static SanctuaryLayout Parse(string json)
        {
            var root = J.Obj(MiniJson.Parse(json), "layout");
            if (J.Str(root, "schema") != "bridge-sanctuary-layout" || J.Int(root, "schema_version") != 1)
                throw new FormatException("unsupported sanctuary layout schema");
            if (J.Str(root, "realm") != "SR1") throw new FormatException("sanctuary layout is not for SR1");
            var layout = new SanctuaryLayout
            {
                KitSha256 = J.Str(root, "kit_sha256"),
                RootName = J.Str(root, "root_name"),
                DeckTopY = J.Float(root, "deck_top_y"),
            };
            var config = J.Obj(root, "config");
            layout.AccessMode = J.Str(config, "sr1_bridge_access_mode");
            if (layout.AccessMode != "ALWAYS" && layout.AccessMode != "ENDGAME") throw new FormatException("access mode");
            layout.GoldCost = J.Int(config, "sr1_gold_plort_gate_cost");
            if (layout.GoldCost < 1 || layout.GoldCost > 999) throw new FormatException("gold cost out of range");
            layout.DynamicRequirements = J.Bool(config, "dynamic_statue_requirements");
            var frame = J.Obj(root, "frame");
            layout.FrameOrigin = J.Vec(frame, "origin", 3);
            layout.HeadingY = J.Float(frame, "heading_y");
            foreach (var item in J.Arr(root, "pieces", 4096)) layout.Pieces.Add(SanctuaryPiece.Parse(J.Obj(item, "piece")));
            foreach (var item in J.Arr(root, "statues", 256)) layout.Statues.Add(SanctuaryStatue.Parse(J.Obj(item, "statue")));
            layout.Gate = SanctuaryGate.Parse(J.Obj(root, "gate"));
            layout.Portal = SanctuaryPortal.Parse(J.Obj(root, "portal"));
            layout.Spawn = SanctuarySpawn.Parse(J.Obj(root, "spawn"));
            var manifest = J.Obj(root, "requirement_manifest");
            foreach (var item in J.Arr(manifest, "required", 256)) layout.RequiredSlimes.Add(J.AsStr(item, "required"));
            foreach (var item in J.Arr(root, "shots", 128)) layout.Shots.Add(SanctuaryShot.Parse(J.Obj(item, "shot")));
            layout.Validate();
            return layout;
        }

        public void Validate()
        {
            if (string.IsNullOrEmpty(RootName) || RootName.Length > 64) throw new FormatException("root name");
            var ids = new HashSet<string>(StringComparer.Ordinal);
            foreach (var p in Pieces)
                if (!ids.Add(p.Id)) throw new FormatException("duplicate piece id " + p.Id);
            foreach (var s in Statues)
                if (!ids.Add(s.Id)) throw new FormatException("duplicate statue id " + s.Id);
            var required = new HashSet<string>(StringComparer.Ordinal);
            foreach (var s in Statues) if (s.Required) required.Add(s.Slime);
            if (required.Count != RequiredSlimes.Count) throw new FormatException("requirement manifest disagrees with statues");
            foreach (var slime in RequiredSlimes)
                if (!required.Contains(slime)) throw new FormatException("requirement manifest names an unknown statue: " + slime);
        }

        public SanctuaryStatue? FindStatue(string slime)
        {
            foreach (var s in Statues) if (s.Slime == slime) return s;
            return null;
        }
    }

    public sealed class SanctuaryPiece
    {
        public string Id = "", Group = "", Name = "", Mesh = "", Collider = "mesh";
        /// <summary>Reveal order when the island appears: rise, span, court, flora or statue.</summary>
        public string Phase = "court";
        public bool CastShadows = true;
        public string[] Materials = new string[0];
        public float[] Position = new float[3], Rotation = new float[4], Scale = new float[3];

        internal static SanctuaryPiece Parse(Dictionary<string, object?> d)
        {
            var p = new SanctuaryPiece
            {
                Id = J.Str(d, "id"), Group = J.Str(d, "group"), Name = J.Str(d, "name"), Mesh = J.Str(d, "mesh"),
                Collider = J.Str(d, "collider"), Position = J.Vec(d, "position", 3), Rotation = J.Vec(d, "rotation", 4),
                Scale = J.Vec(d, "scale", 3),
            };
            if (p.Collider != "mesh" && p.Collider != "none" && p.Collider != "box") throw new FormatException("collider kind");
            if (d.TryGetValue("phase", out var phase) && phase != null) p.Phase = J.AsStr(phase, "phase");
            if (p.Phase != "rise" && p.Phase != "span" && p.Phase != "court" && p.Phase != "flora" && p.Phase != "statue") throw new FormatException("reveal phase");
            if (d.TryGetValue("shadows", out var shadows) && shadows != null) p.CastShadows = shadows is bool b ? b : throw new FormatException("shadows must be a boolean");
            var mats = new List<string>();
            foreach (var m in J.Arr(d, "materials", 16)) mats.Add(J.AsStr(m, "material"));
            if (mats.Count == 0) throw new FormatException("piece " + p.Id + " has no materials");
            p.Materials = mats.ToArray();
            return p;
        }
    }

    public sealed class SanctuaryComponent
    {
        public string Type = "";
        public bool Trigger;
        public float[] Center = new float[3];
        public float Radius, Height;
        public float[] Size = new float[3];
        public int LightType;
        public float[] Color = new float[4];
        public float Intensity, Range, SpotAngle;

        internal static SanctuaryComponent Parse(Dictionary<string, object?> d)
        {
            var c = new SanctuaryComponent { Type = J.Str(d, "type") };
            switch (c.Type)
            {
                case "SphereCollider":
                    c.Trigger = J.Bool(d, "trigger"); c.Center = J.Vec(d, "center", 3); c.Radius = J.Float(d, "radius"); break;
                case "BoxCollider":
                    c.Trigger = J.Bool(d, "trigger"); c.Center = J.Vec(d, "center", 3); c.Size = J.Vec(d, "size", 3); break;
                case "CapsuleCollider":
                    c.Trigger = J.Bool(d, "trigger"); c.Center = J.Vec(d, "center", 3); c.Radius = J.Float(d, "radius"); c.Height = J.Float(d, "height"); break;
                case "Light":
                    c.LightType = J.Int(d, "light_type"); c.Color = J.Vec(d, "color", 4); c.Intensity = J.Float(d, "intensity");
                    c.Range = J.Float(d, "range"); c.SpotAngle = J.Float(d, "spot_angle"); break;
                default: throw new FormatException("unsupported template component " + c.Type);
            }
            return c;
        }
    }

    public sealed class SanctuaryNode
    {
        public string Path = "", Name = "", Mesh = "";
        public bool Active = true;
        public float[] Position = new float[3], Rotation = new float[4], Scale = new float[3];
        public string[] Materials = new string[0];
        public List<SanctuaryComponent> Components = new List<SanctuaryComponent>();

        internal static SanctuaryNode Parse(Dictionary<string, object?> d)
        {
            var n = new SanctuaryNode { Path = J.Str(d, "path"), Name = J.Str(d, "name"), Active = J.Bool(d, "active") };
            var local = J.Obj(d, "local");
            n.Position = J.Vec(local, "position", 3); n.Rotation = J.Vec(local, "rotation", 4); n.Scale = J.Vec(local, "scale", 3);
            if (d.TryGetValue("mesh", out var mesh) && mesh != null)
            {
                n.Mesh = J.AsStr(mesh, "mesh");
                var mats = new List<string>();
                foreach (var m in J.Arr(d, "materials", 16)) mats.Add(J.AsStr(m, "material"));
                n.Materials = mats.ToArray();
            }
            if (d.TryGetValue("components", out var comps) && comps != null)
                foreach (var c in J.AsArr(comps, "components", 16)) n.Components.Add(SanctuaryComponent.Parse(J.Obj(c, "component")));
            return n;
        }
    }

    public sealed class SanctuaryTemplate
    {
        public string Construct = "", CatchName = "";
        public List<SanctuaryNode> Nodes = new List<SanctuaryNode>();

        internal static SanctuaryTemplate Parse(Dictionary<string, object?> d)
        {
            var t = new SanctuaryTemplate { Construct = J.Str(d, "construct") };
            if (d.TryGetValue("catch_name", out var cn) && cn != null) t.CatchName = J.AsStr(cn, "catch_name");
            foreach (var item in J.Arr(d, "nodes", 64)) t.Nodes.Add(SanctuaryNode.Parse(J.Obj(item, "node")));
            if (t.Nodes.Count == 0) throw new FormatException("empty statue template");
            return t;
        }
    }

    public sealed class SanctuaryStatue
    {
        public string Id = "", Slime = "", Plort = "", Status = "", Placement = "", StatueMaterial = "", EyesMaterial = "", PlortDisplayMaterial = "";
        public string DetailTexture = "";
        public int PlortId;
        public bool Required, Optional, Hidden;
        public float Yaw;
        public float[] Position = new float[3], Rotation = new float[4];
        public float[]? LightColor, TintTop, TintMiddle;
        public SanctuaryTemplate Template = new SanctuaryTemplate();

        internal static SanctuaryStatue Parse(Dictionary<string, object?> d)
        {
            var s = new SanctuaryStatue
            {
                Id = J.Str(d, "id"), Slime = J.Str(d, "slime"), Plort = J.Str(d, "plort"), Status = J.Str(d, "status"),
                Placement = J.Str(d, "placement"), PlortId = J.Int(d, "plort_id"), Required = J.Bool(d, "required"),
                Optional = J.Bool(d, "optional"), Hidden = J.Bool(d, "hidden"), Yaw = J.Float(d, "yaw"),
                Position = J.Vec(d, "position", 3), Rotation = J.Vec(d, "rotation", 4),
                StatueMaterial = J.Str(d, "statue_material"), EyesMaterial = J.Str(d, "eyes_material"),
                PlortDisplayMaterial = J.Str(d, "plort_display_material"),
                Template = SanctuaryTemplate.Parse(J.Obj(d, "template")),
            };
            if (s.Status != "native" && s.Status != "synthesized") throw new FormatException("statue status");
            if (d.TryGetValue("light_color", out var lc) && lc != null) s.LightColor = J.VecOf(lc, 4);
            if (d.TryGetValue("detail_texture", out var dt) && dt != null) s.DetailTexture = J.AsStr(dt, "detail_texture");
            if (d.TryGetValue("tint", out var tint) && tint != null)
            {
                var t = J.Obj(tint, "tint");
                if (t.TryGetValue("top", out var top) && top != null) s.TintTop = J.VecOf(top, 4);
                if (t.TryGetValue("middle", out var mid) && mid != null) s.TintMiddle = J.VecOf(mid, 4);
            }
            return s;
        }
    }

    public sealed class SanctuaryGate
    {
        public string Plort = "";
        public int PlortId, Cost;
        public bool Enabled;
        public float Yaw, BarrierYaw, BarrierWidth, BarrierHeight;
        public float[] Position = new float[3], BarrierPosition = new float[3];
        public string FrameMesh = "", DoorMesh = "";
        public string[] FrameMaterials = new string[0], DoorMaterials = new string[0];
        public SanctuaryStatue Statue = new SanctuaryStatue();
        public SanctuaryTablet? Tablet;
        public SanctuaryFigure? Figure;

        internal static SanctuaryGate Parse(Dictionary<string, object?> d)
        {
            var g = new SanctuaryGate
            {
                Plort = J.Str(d, "plort"), PlortId = J.Int(d, "plort_id"), Cost = J.Int(d, "cost"), Enabled = J.Bool(d, "enabled"),
                Position = J.Vec(d, "position", 3), Yaw = J.Float(d, "yaw"), Statue = SanctuaryStatue.Parse(J.Obj(d, "statue")),
            };
            if (d.TryGetValue("tablet", out var tablet) && tablet != null) g.Tablet = SanctuaryTablet.Parse(J.Obj(tablet, "tablet"));
            if (d.TryGetValue("figure", out var figure) && figure != null) g.Figure = SanctuaryFigure.Parse(J.Obj(figure, "figure"));
            var b = J.Obj(d, "barrier");
            g.BarrierPosition = J.Vec(b, "position", 3); g.BarrierYaw = J.Float(b, "yaw");
            g.BarrierWidth = J.Float(b, "width"); g.BarrierHeight = J.Float(b, "height");
            g.FrameMesh = J.Str(b, "frame_mesh"); g.DoorMesh = J.Str(b, "door_mesh");
            g.FrameMaterials = StrArray(b, "frame_materials"); g.DoorMaterials = StrArray(b, "door_materials");
            return g;
        }

        private static string[] StrArray(Dictionary<string, object?> d, string key)
        {
            var list = new List<string>();
            foreach (var item in J.Arr(d, key, 16)) list.Add(J.AsStr(item, key));
            if (list.Count == 0) throw new FormatException(key + " is empty");
            return list.ToArray();
        }
    }

    /// <summary>A stone tablet with a lore message (title glyph, lines, signature) standing next to the depositor.</summary>
    public sealed class SanctuaryTablet
    {
        public string Id = "", Mesh = "", Title = "", Signature = "";
        public string[] Materials = new string[0], Lines = new string[0];
        public float Height;
        public float[] Position = new float[3], Rotation = new float[4], Scale = new float[3];

        internal static SanctuaryTablet Parse(Dictionary<string, object?> d)
        {
            var t = new SanctuaryTablet
            {
                Id = J.Str(d, "id"), Mesh = J.Str(d, "mesh"), Title = J.Str(d, "title"), Signature = J.Str(d, "signature"),
                Height = J.Float(d, "height"), Position = J.Vec(d, "position", 3), Rotation = J.Vec(d, "rotation", 4), Scale = J.Vec(d, "scale", 3),
            };
            var mats = new List<string>();
            foreach (var m in J.Arr(d, "materials", 16)) mats.Add(J.AsStr(m, "material"));
            var lines = new List<string>();
            foreach (var l in J.Arr(d, "lines", 16)) lines.Add(J.AsStr(l, "line"));
            if (mats.Count == 0 || lines.Count == 0) throw new FormatException("tablet needs materials and text");
            t.Materials = mats.ToArray(); t.Lines = lines.ToArray();
            return t;
        }
    }

    /// <summary>A frozen native slime shown on the depositor's plinth (the game's own mesh and material).</summary>
    public sealed class SanctuaryFigure
    {
        public string Kind = "", Slime = "";
        public float Scale = 1f, Lift;

        internal static SanctuaryFigure Parse(Dictionary<string, object?> d)
        {
            var f = new SanctuaryFigure { Kind = J.Str(d, "kind"), Slime = J.Str(d, "slime"), Scale = J.Float(d, "scale"), Lift = J.Float(d, "lift") };
            if (f.Kind != "native_slime" || f.Scale < 0.1f || f.Scale > 6f) throw new FormatException("figure");
            return f;
        }
    }

    public sealed class SanctuaryPortal
    {
        public string PortalId = "", Destination = "", Requires = "";
        public bool RequiresStatues => Requires != "none";
        public float Yaw, TriggerRadius;
        public float[] Position = new float[3];
        public string RingMesh = "", MembraneMaterial = "", MembraneFallback = "";
        public string[] RingMaterials = new string[0];
        public int RingCount;
        public float RingRadius, RingCenterHeight, RingScale, MembraneRadius;

        internal static SanctuaryPortal Parse(Dictionary<string, object?> d)
        {
            var p = new SanctuaryPortal
            {
                PortalId = J.Str(d, "portal_id"), Destination = J.Str(d, "destination"), Requires = J.Str(d, "requires"),
                Yaw = J.Float(d, "yaw"), TriggerRadius = J.Float(d, "trigger_radius"), Position = J.Vec(d, "position", 3),
            };
            var ring = J.Obj(d, "ring");
            p.RingMesh = J.Str(ring, "mesh"); p.RingCount = J.Int(ring, "count"); p.RingRadius = J.Float(ring, "radius");
            p.RingCenterHeight = J.Float(ring, "center_height"); p.RingScale = J.Float(ring, "scale");
            var mats = new List<string>();
            foreach (var m in J.Arr(ring, "materials", 16)) mats.Add(J.AsStr(m, "material"));
            if (mats.Count == 0 || p.RingCount < 3 || p.RingCount > 64) throw new FormatException("portal ring");
            p.RingMaterials = mats.ToArray();
            var membrane = J.Obj(d, "membrane");
            p.MembraneMaterial = J.Str(membrane, "material"); p.MembraneFallback = J.Str(membrane, "fallback_material");
            p.MembraneRadius = J.Float(membrane, "radius");
            return p;
        }
    }

    public sealed class SanctuarySpawn
    {
        public float Yaw;
        public float[] Position = new float[3];
        internal static SanctuarySpawn Parse(Dictionary<string, object?> d) =>
            new SanctuarySpawn { Yaw = J.Float(d, "yaw"), Position = J.Vec(d, "position", 3) };
    }

    public sealed class SanctuaryShot
    {
        public string Name = "";
        public float[] Position = new float[3], LookAt = new float[3];
        internal static SanctuaryShot Parse(Dictionary<string, object?> d) =>
            new SanctuaryShot { Name = J.Str(d, "name"), Position = J.Vec(d, "position", 3), LookAt = J.Vec(d, "look_at", 3) };
    }

    /// <summary>Strict accessors over <see cref="MiniJson"/> trees.</summary>
    internal static class J
    {
        public static Dictionary<string, object?> Obj(object? value, string what)
        {
            if (value is Dictionary<string, object?> d) return d;
            throw new FormatException(what + " must be an object");
        }
        public static Dictionary<string, object?> Obj(Dictionary<string, object?> d, string key) => Obj(Get(d, key), key);
        public static object? Get(Dictionary<string, object?> d, string key)
        {
            if (!d.TryGetValue(key, out var v)) throw new FormatException("missing field " + key);
            return v;
        }
        public static string Str(Dictionary<string, object?> d, string key) => AsStr(Get(d, key), key);
        public static string AsStr(object? v, string what)
        {
            if (v is string s && s.Length <= 4096) return s;
            throw new FormatException(what + " must be a string");
        }
        public static double Num(object? v, string what)
        {
            if (v is long l) return l;
            if (v is double x && !double.IsNaN(x) && !double.IsInfinity(x)) return x;
            throw new FormatException(what + " must be a finite number");
        }
        public static float Float(Dictionary<string, object?> d, string key) => (float)Num(Get(d, key), key);
        public static int Int(Dictionary<string, object?> d, string key)
        {
            var v = Get(d, key);
            if (v is long l && l >= int.MinValue && l <= int.MaxValue) return (int)l;
            throw new FormatException(key + " must be an integer");
        }
        public static bool Bool(Dictionary<string, object?> d, string key)
        {
            if (Get(d, key) is bool b) return b;
            throw new FormatException(key + " must be a boolean");
        }
        public static List<object?> AsArr(object? v, string what, int max)
        {
            if (v is List<object?> list && list.Count <= max) return list;
            throw new FormatException(what + " must be an array (max " + max + ")");
        }
        public static List<object?> Arr(Dictionary<string, object?> d, string key, int max) => AsArr(Get(d, key), key, max);
        public static float[] VecOf(object? v, int n)
        {
            var list = AsArr(v, "vector", n);
            if (list.Count != n) throw new FormatException("vector must have " + n + " numbers");
            var result = new float[n];
            for (var i = 0; i < n; i++) result[i] = (float)Num(list[i], "vector component");
            return result;
        }
        public static float[] Vec(Dictionary<string, object?> d, string key, int n) => VecOf(Get(d, key), n);
    }
}
