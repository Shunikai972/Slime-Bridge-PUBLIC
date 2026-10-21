"""Procedural SlimeBridge gateway: a chunky rounded portal ring over a short walkable bridge.

Original SlimeBridge design (no game geometry, textures or UI are used). The player walks along
+Z/-Z over a deck with gentle ramps at both ends and passes through a round ring whose legs
plunge into two pedestals beside the deck. The structure is symmetric front/back, so it reads
and works from both sides.

Frame (glTF 2.0 convention): metres, +Y up, +Z forward (the "front" side), right-handed, so +X is
the asset's own left; a player approaching the front (standing at +Z, looking toward -Z) sees
-X on their left. The origin/pivot is the deck centre on the ground plane (y = 0).

``generate_portal(params)`` never raises for bad input: invalid parameters or a broken gameplay
contract return a ``PortalModel`` with ``ok == False`` and unified diagnostics, never NaN
geometry. Outputs are deterministic: identical parameters give identical, rounded data.
"""

import dataclasses
import math
import re
from dataclasses import dataclass, field

from bridge.content.canonical import sha256_json
from bridge.core import diagnostics as D

from . import mesh as M
from . import primitives as P
from . import vec as V
from . import volumes as B
from .palettes import PALETTES, SEMANTIC_MATERIALS

GENERATOR = 'slimebridge.meshgen.portal'
GENERATOR_VERSION = '1.0.0'
SCHEMA = 'slimebridge-portal-3d'
SCHEMA_VERSION = 1
VISUAL_EVIDENCE = 'NEEDS_LOCAL_VISUAL_PROOF'
SUBSYSTEM = 'meshgen'

TOE_HEIGHT = 0.04          # ramp lip height where it meets the ground (m)
COLLAR_INSET = 0.025       # collars / keystone may reach this far inside the ring's inner radius
COLLAR_OUT = 0.1           # collars stand proud of the ring's outer radius
COLLAR_DEPTH = 0.07        # collars stand proud of the ring faces
PED_CAP = 0.12             # pedestal cap plate thickness
PLINTH_FRACTION = 0.38     # pedestal plinth tier height (fraction of pedestal height)
PLINTH_GROW = 0.18         # plinth tier overhang (outward in x, both ways in z)
POST_RADIUS = 0.07
HANDRAIL_RADIUS = 0.062
KNOB_RADIUS = 0.095
PLANK_LENGTH = 0.42        # walking planks: nominal pitch along the path
PLANK_GAP = 0.035
PLANK_THICKNESS = 0.06
PLANK_PROUD = 0.015        # plank tops stand this far above the structural deck surface
TRIGGER_MARGIN = 0.05      # trigger stays this far inside the effective opening radius
DECK_GAP = 0.03            # clearance between deck sides and ring legs / pedestals
RAIL_GAP = 0.45            # rails stop this far in front of / behind the collar faces
SPAWN_CLEARANCE = 0.6      # spawn sockets must stand this far outside the trigger volume
COLLISION_RING_BOXES = 14
COLLISION_TOLERANCE = 0.06
MAX_EXTENT = 60.0
REQUIRED_SOCKETS = ('socket.energy_core', 'socket.sign_left', 'socket.sign_right', 'socket.vfx_center',
                    'socket.spawn_front', 'socket.spawn_back')

CONVENTIONS = {
    'units': 'metres',
    'up_axis': '+Y',
    'forward_axis': '+Z',
    'handedness': 'right',
    'convention': 'glTF 2.0 (+Y up, +Z forward/front, +X is the asset-left, right-handed)',
    'pivot': 'deck centre on the ground plane (y = 0)',
    'front_side': '+Z',
    'back_side': '-Z',
    'viewer_left_from_front': '-X',
    'winding': 'counter-clockwise front faces',
    'uv': 'world-scale metres (1 UV unit = 1 m) except the portal energy disc (0..1 planar)',
}


@dataclass(frozen=True)
class PortalParams:
    """Every tunable of the gateway. Lengths in metres, angles in degrees."""
    variant: str = 'meadow'
    palette: str = 'meadow'
    ring_inner_radius: float = 2.05
    ring_tube_width: float = 0.62
    ring_tube_depth: float = 0.82
    bevel_radius: float = 0.16
    ring_center_lift: float = 1.15
    ring_segments: int = 96
    bevel_segments: int = 4
    deck_length: float = 3.4
    deck_width: float = 3.0
    deck_thickness: float = 0.3
    deck_height: float = 0.45
    ramp_slope_deg: float = 14.0
    curb_width: float = 0.16
    curb_height: float = 0.1
    rail_height: float = 0.95
    rail_post_spacing: float = 1.1
    pedestal_width: float = 1.15
    pedestal_height: float = 1.0
    pedestal_depth: float = 1.4
    core_radius: float = 0.2
    lod_count: int = 3
    min_clear_width: float = 2.2
    min_clear_height: float = 2.6
    min_walkable_width: float = 2.0
    max_ramp_slope_deg: float = 20.0

    def to_dict(self):
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}


PARAM_NAMES = tuple(f.name for f in dataclasses.fields(PortalParams))

# name -> (kind, minimum, maximum)
PARAM_LIMITS = {
    'ring_inner_radius': ('float', 1.2, 4.5),
    'ring_tube_width': ('float', 0.15, 1.2),
    'ring_tube_depth': ('float', 0.2, 1.6),
    'bevel_radius': ('float', 0.01, 0.4),
    'ring_center_lift': ('float', 0.0, 3.5),
    'ring_segments': ('int', 12, 256),
    'bevel_segments': ('int', 1, 8),
    'deck_length': ('float', 1.5, 12.0),
    'deck_width': ('float', 1.2, 6.0),
    'deck_thickness': ('float', 0.1, 1.0),
    'deck_height': ('float', 0.1, 1.5),
    'ramp_slope_deg': ('float', 3.0, 45.0),
    'curb_width': ('float', 0.05, 0.5),
    'curb_height': ('float', 0.02, 0.4),
    'rail_height': ('float', 0.5, 1.5),
    'rail_post_spacing': ('float', 0.5, 3.0),
    'pedestal_width': ('float', 0.4, 3.0),
    'pedestal_height': ('float', 0.3, 3.0),
    'pedestal_depth': ('float', 0.4, 3.0),
    'core_radius': ('float', 0.05, 0.6),
    'lod_count': ('int', 1, 3),
    'min_clear_width': ('float', 0.5, 6.0),
    'min_clear_height': ('float', 1.0, 6.0),
    'min_walkable_width': ('float', 0.5, 6.0),
    'max_ramp_slope_deg': ('float', 1.0, 45.0),
}

# Named looks: only palette ids and proportions change, always through parameters.
VARIANTS = {
    'meadow': {},
    # taller, slimmer ring on a narrower bridge with higher pedestals
    'twilight': {'palette': 'twilight', 'ring_inner_radius': 2.15, 'ring_tube_width': 0.54,
                 'ring_tube_depth': 0.76, 'bevel_radius': 0.15, 'ring_center_lift': 1.2, 'deck_width': 2.9,
                 'pedestal_height': 1.15, 'rail_height': 1.0, 'core_radius': 0.18},
    # extra chunky ring, wider and longer bridge, broad pedestals
    'coral': {'palette': 'coral', 'ring_inner_radius': 2.1, 'ring_tube_width': 0.72, 'ring_tube_depth': 0.94,
              'bevel_radius': 0.2, 'ring_center_lift': 1.12, 'deck_width': 3.1, 'deck_length': 3.8,
              'pedestal_width': 1.3, 'pedestal_depth': 1.6, 'core_radius': 0.24},
}

LOD_LEVELS = (
    {'level': 0, 'ring': 1.0, 'corner': 1.0, 'round': 1.0, 'fillet': 3, 'details': True},
    {'level': 1, 'ring': 0.5, 'corner': 0.5, 'round': 0.5, 'fillet': 2, 'details': True},
    {'level': 2, 'ring': 0.25, 'corner': 0.25, 'round': 0.34, 'fillet': 1, 'details': False},
)

_NAME = re.compile(r'^[a-z][a-z0-9_-]{0,31}$')


@dataclass
class PortalModel:
    params: object = None
    params_hash: str = None
    lods: list = field(default_factory=list)       # [{'level', 'detail', 'parts': [MeshPart]}]
    collision: list = field(default_factory=list)  # oriented boxes (see volumes.py)
    trigger: dict = None
    sockets: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    diagnostics: list = field(default_factory=list)

    @property
    def ok(self):
        return bool(self.lods) and not D.has_errors(self.diagnostics)

    def parts(self, level=0):
        return self.lods[level]['parts']


def _jsonable(value):
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else repr(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return repr(value)


def _diag(code, severity, message, subject=None, **details):
    return D.make(code, severity, SUBSYSTEM, message, subject=subject, details=_jsonable(details))


# --- parameters ---------------------------------------------------------------------------------

def variant_params(name='meadow', **overrides):
    """PortalParams for a named variant (plus explicit overrides). Unknown names raise KeyError."""
    values = dict(VARIANTS[name])
    values.setdefault('variant', name)
    values.update(overrides)
    return PortalParams(**values)


def validate_params(params):
    """Diagnostics for a PortalParams instance (types, finiteness, ranges, cross-field rules)."""
    out = []
    if not isinstance(params, PortalParams):
        return [_diag('PARAMS_INVALID', 'error', 'expected PortalParams', subject='params')]
    for name in PARAM_NAMES:
        value = getattr(params, name)
        if name == 'variant':
            if not isinstance(value, str) or not _NAME.fullmatch(value):
                out.append(_diag('PARAM_INVALID_NAME', 'error', 'variant must match [a-z][a-z0-9_-]{0,31}',
                                 subject=name, value=value))
            continue
        if name == 'palette':
            if not isinstance(value, str) or value not in PALETTES:
                out.append(_diag('PARAM_UNKNOWN_PALETTE', 'error', 'unknown palette id', subject=name,
                                 value=value, allowed=sorted(PALETTES)))
            continue
        kind, lo, hi = PARAM_LIMITS[name]
        if kind == 'int' and type(value) is not int:
            out.append(_diag('PARAM_TYPE', 'error', 'integer required', subject=name, value=value))
        elif kind == 'float' and type(value) not in (int, float):
            out.append(_diag('PARAM_TYPE', 'error', 'number required', subject=name, value=value))
        elif type(value) is float and not math.isfinite(value):
            out.append(_diag('PARAM_NOT_FINITE', 'error', 'value must be finite', subject=name, value=value))
        elif not lo <= value <= hi:
            out.append(_diag('PARAM_OUT_OF_RANGE', 'error', f'{name} must be within [{lo}, {hi}]', subject=name,
                             value=value, minimum=lo, maximum=hi))
    if out:
        return out
    p = params
    if p.bevel_radius >= 0.5 * min(p.ring_tube_width, p.ring_tube_depth):
        out.append(_diag('PARAM_BEVEL_TOO_LARGE', 'error', 'bevel radius must be below half the tube width/depth',
                         subject='bevel_radius', value=p.bevel_radius))
    if p.deck_thickness > p.deck_height:
        out.append(_diag('PARAM_DECK_BELOW_GROUND', 'error', 'deck thickness exceeds deck height',
                         subject='deck_thickness'))
    return out


def params_from_dict(raw):
    """``(PortalParams | None, diagnostics)`` from untrusted JSON. Never raises."""
    if not isinstance(raw, dict):
        return None, [_diag('PARAMS_INVALID', 'error', 'parameters must be a JSON object', subject='params')]
    out = [_diag('PARAM_UNKNOWN', 'error', 'unknown parameter', subject=str(key))
           for key in sorted(set(raw) - set(PARAM_NAMES), key=str)]
    params = PortalParams(**{k: raw[k] for k in PARAM_NAMES if k in raw})
    out.extend(validate_params(params))
    return (None if D.has_errors(out) else params), out


def params_hash(params):
    return sha256_json(params.to_dict())


# --- layout: every derived dimension + the gameplay contract ------------------------------------

def compute_layout(p):
    """Derived dimensions and contract diagnostics for valid ``PortalParams`` (pure math)."""
    out = []
    ri, tw, td = p.ring_inner_radius, p.ring_tube_width, p.ring_tube_depth
    rm, ro, reff = ri + 0.5 * tw, ri + tw, ri - COLLAR_INSET
    hd, T, W, L = p.deck_height, p.deck_thickness, p.deck_width, p.deck_length
    cy = hd + p.ring_center_lift
    slope = math.radians(p.ramp_slope_deg)
    ramp_len = (hd - TOE_HEIGHT) / math.tan(slope)
    zc = 0.5 * L
    ztoe = zc + ramp_len
    walk = W - 2.0 * p.curb_width

    def chord(y, r=reff):
        dy = y - cy
        return 2.0 * math.sqrt(r * r - dy * dy) if abs(dy) < r else 0.0

    clear_h = cy + reff - hd
    clear_w = min(chord(hd), chord(hd + p.min_clear_height)) if p.min_clear_height < clear_h else 0.0
    lay = dict(ri=ri, tw=tw, td=td, rm=rm, ro=ro, reff=reff, hd=hd, T=T, W=W, L=L, cy=cy, slope=slope,
               ramp_len=ramp_len, zc=zc, ztoe=ztoe, walk=walk, clear_h=clear_h, clear_w=clear_w,
               fillet=min(0.35, 0.3 * ramp_len, 0.3 * zc))

    if p.ramp_slope_deg > p.max_ramp_slope_deg:
        out.append(_diag('RAMP_TOO_STEEP', 'error', 'ramp slope exceeds the walkable maximum', subject='ramp_slope_deg',
                         slope_deg=p.ramp_slope_deg, max_deg=p.max_ramp_slope_deg))
    if clear_w < p.min_clear_width:
        out.append(_diag('OPENING_TOO_NARROW', 'error', 'portal opening is narrower than the player minimum',
                         subject='opening', clear_width=clear_w, min_clear_width=p.min_clear_width))
    if clear_h < p.min_clear_height:
        out.append(_diag('OPENING_TOO_LOW', 'error', 'portal opening is lower than the player minimum',
                         subject='opening', clear_height=clear_h, min_clear_height=p.min_clear_height))
    if walk < p.min_walkable_width:
        out.append(_diag('WALKABLE_TOO_NARROW', 'error', 'walkable bridge width is below the minimum',
                         subject='deck', walkable_width=walk, min_walkable_width=p.min_walkable_width))

    # Ring legs end inside the pedestals beside the deck.
    ped_top = p.pedestal_height + 0.8 * PED_CAP
    y_end = 0.45 * p.pedestal_height
    if (y_end - cy) / rm <= -0.98:
        out.append(_diag('RING_MISSES_PEDESTAL', 'error', 'ring legs do not reach down into the pedestals',
                         subject='pedestal_height'))
        return lay, out
    if ped_top > cy - 0.1:
        out.append(_diag('PEDESTAL_TOO_TALL', 'error', 'pedestals reach the ring centre height',
                         subject='pedestal_height'))
        return lay, out
    a_end = math.asin((y_end - cy) / rm)
    a_top = math.asin((ped_top - cy) / rm)
    samples = [a_end + (0.5 * math.pi - a_end) * k / 240 for k in range(241)]
    radii = [reff + (ro - reff) * k / 6 for k in range(7)]  # the whole tube section, incl. the end cap
    leg = [(r * math.cos(a), cy + r * math.sin(a)) for a in samples for r in radii]
    inside = [pt for pt in leg if pt[1] <= ped_top]
    x_lo, x_hi = min(x for x, _ in inside), max(x for x, _ in inside)
    half = 0.5 * p.pedestal_width
    margin = 0.06
    lo_c, hi_c = x_hi + margin - half, x_lo - margin + half
    x_ped = max(0.5 * (x_lo + x_hi), 0.5 * W + DECK_GAP + half)
    if lo_c > hi_c:
        out.append(_diag('PEDESTAL_TOO_NARROW', 'error', 'pedestal cannot contain the ring leg',
                         subject='pedestal_width', needed=x_hi - x_lo + 2 * margin, width=p.pedestal_width))
    elif x_ped > hi_c:
        out.append(_diag('PEDESTAL_OVERLAPS_DECK', 'error',
                         'pedestal cannot hold the ring leg without touching the deck',
                         subject='pedestal_width', pedestal_inner_x=hi_c - half, deck_half_width=0.5 * W))
    if 0.5 * p.pedestal_depth < 0.5 * td + COLLAR_DEPTH + 0.05:
        out.append(_diag('PEDESTAL_TOO_SHALLOW', 'error', 'pedestal is thinner than the ring',
                         subject='pedestal_depth'))
    band = [x for x, y in leg if hd - T - 0.01 <= y <= hd + p.curb_height + 0.01]
    if band and min(band) < 0.5 * W + DECK_GAP:
        out.append(_diag('RING_INTRUDES_DECK', 'error',
                         'ring legs cut through the deck; lower the ring or narrow the deck',
                         subject='ring_center_lift', ring_x=min(band), deck_half_width=0.5 * W))

    # Passage / trigger: the largest walkable rectangle inside the opening, with a margin.
    rt = reff - TRIGGER_MARGIN
    wp = min(walk, chord(hd, rt))
    top = cy + math.sqrt(max(0.0, rt * rt - 0.25 * wp * wp))
    kw, k0, k1 = max(0.5, 0.9 * tw + 0.14), cy + reff, cy + ro + 0.32
    kd = 0.5 * td + 0.1
    if p.core_radius > 0.9 * min(kw, 0.5 * (k1 - k0)):
        out.append(_diag('PARAM_CORE_TOO_LARGE', 'error', 'energy core does not fit its keystone housing',
                         subject='core_radius'))
    z_spawn = 0.5 * td + COLLAR_DEPTH + 1.1
    lay.update(ped_top=ped_top, y_end=y_end, a_end=a_end, a_top=a_top, x_ped=x_ped, x_lo=x_lo, x_hi=x_hi,
               passage_w=wp, passage_top=top, passage_h=top - hd, kw=kw, ky=0.5 * (k0 + k1), kh=0.5 * (k1 - k0),
               kd=kd, x_pod=ro + 0.1, z_gap=0.5 * td + COLLAR_DEPTH + RAIL_GAP,
               z_far=zc + 0.8 * ramp_len, z_spawn=z_spawn, x_curb=0.5 * W - 0.5 * p.curb_width,
               foundation_top=hd - T + 0.02)
    if z_spawn > ztoe - 0.4:
        out.append(_diag('SPAWN_OFF_BRIDGE', 'error', 'bridge is too short for spawn points outside the trigger',
                         subject='deck_length'))
    if wp <= 0.5 or top - hd <= 0.5:
        out.append(_diag('PASSAGE_TOO_SMALL', 'error', 'no usable passage through the ring', subject='opening'))
    return lay, out


def surface_y(lay, z):
    """Walking-surface height at ``z`` (piecewise linear; the crest fillet sits <= 2 cm lower)."""
    az = abs(z)
    if az <= lay['zc']:
        return lay['hd']
    return max(TOE_HEIGHT, lay['hd'] - (az - lay['zc']) * math.tan(lay['slope']))


def _deck_path(lay, fillet_samples):
    zc, hd, T, s, f = lay['zc'], lay['hd'], lay['T'], lay['slope'], lay['fillet']
    ztoe = lay['ztoe']

    def thick(z):
        az = abs(z)
        return T if az <= zc else T + (TOE_HEIGHT - T) * (az - zc) / (ztoe - zc)
    a, c, b = (zc - f, hd), (zc, hd), (zc + f * math.cos(s), hd - f * math.sin(s))
    front = [(a[0], a[1], T)]
    n = fillet_samples + 1
    for k in range(1, n):
        t = k / n
        z = (1 - t) ** 2 * a[0] + 2 * (1 - t) * t * c[0] + t * t * b[0]
        y = (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * c[1] + t * t * b[1]
        front.append((z, y, thick(z)))
    front.append((b[0], b[1], thick(b[0])))
    front.append((ztoe, TOE_HEIGHT, TOE_HEIGHT))
    return [(-z, y, t) for z, y, t in reversed(front)] + front


# --- geometry -----------------------------------------------------------------------------------

def _emerge_angle(lay, radius):
    """Angle where a circle of ``radius`` around the ring centre rises out of the pedestal caps
    (never before the ring's own end angle, never a domain error)."""
    s = (lay['ped_top'] + 0.05 - lay['cy']) / radius
    return max(lay['a_end'], math.asin(max(-1.0, min(0.0, s))))


def _detail(p, spec):
    corner = max(1, int(round(p.bevel_segments * spec['corner'])))
    return {'level': spec['level'], 'ring_segments': max(12, int(round(p.ring_segments * spec['ring']))),
            'corner_segments': corner, 'box_bevel_segments': max(1, int(round(corner * 0.75))),
            'round_segments': max(6, int(round(p.ring_segments / 4 * spec['round']))),
            'fillet_samples': spec['fillet'], 'details': spec['details']}


def _build_lod(p, lay, det):
    rs, cs, bs, ns = det['ring_segments'], det['corner_segments'], det['box_bevel_segments'], det['round_segments']
    ri, tw, td, rm, ro, reff, cy = (lay[k] for k in ('ri', 'tw', 'td', 'rm', 'ro', 'reff', 'cy'))
    hd, W, zc = lay['hd'], lay['W'], lay['zc']
    centre = (0.0, cy, 0.0)
    a0, a1 = lay['a_end'], math.pi - lay['a_end']
    parts = {}

    def add(name, material, *pieces):
        parts.setdefault(name, (material, []))[1].extend(pieces)

    # Ring: rounded-rectangle tube from one pedestal, over the top, into the other.
    add('ring.frame', 'frame_primary', P.ring_arc('ring.frame', 'frame_primary', ri, tw, td, p.bevel_radius, rs, cs,
                                                  start=a0, end=a1, center=centre))
    seam_a = _emerge_angle(lay, ro)
    add('ring.seam', 'frame_secondary', P.ring_arc('ring.seam', 'frame_secondary', ro - 0.035, 0.07, 0.18, 0.03, rs,
                                                   max(1, cs // 2), start=seam_a, end=math.pi - seam_a, center=centre))
    trim_a = _emerge_angle(lay, ri + 0.1)
    for z in (0.5 * td, -0.5 * td):
        add('ring.trim', 'emissive_trim', P.ring_arc('ring.trim', 'emissive_trim', ri + 0.07, 0.07, 0.05, 0.02, rs,
                                                     max(1, cs // 2), start=trim_a, end=math.pi - trim_a,
                                                     center=(0.0, cy, z)))
    span = 0.3 / rm
    a_low = math.asin((lay['ped_top'] + 0.2 - cy) / rm) + 0.5 * span
    collar_w = ro + COLLAR_OUT - reff
    for a in (a_low, math.pi - a_low, 0.5 * math.pi - 0.75, 0.5 * math.pi + 0.75):
        add('ring.collars', 'accent', P.ring_arc('ring.collars', 'accent', reff, collar_w, td + 2 * COLLAR_DEPTH,
                                                 p.bevel_radius + 0.02, max(rs, 48), cs, start=a - 0.5 * span,
                                                 end=a + 0.5 * span, center=centre))
    kw, ky, kh, kd = lay['kw'], lay['ky'], lay['kh'], lay['kd']
    add('ring.keystone', 'frame_secondary',
        P.rounded_box('ring.keystone', 'frame_secondary', (kw, kh, kd), min(0.22, 0.6 * kh), bs + 1,
                      V.translation((0.0, ky, 0.0))))
    cr = p.core_radius
    tip = kd + 0.6 * cr
    add('core.energy', 'portal_energy',
        P.capsule('core.energy', 'portal_energy', (0.0, ky, -max(0.0, tip - cr)), (0.0, ky, max(0.0, tip - cr)),
                  cr, ns, max(2, cs)))
    x_pod = lay['x_pod']
    for sx in (1.0, -1.0):
        add('ring.pods', 'frame_secondary',
            P.rounded_box('ring.pods', 'frame_secondary', (0.17, 0.38, 0.5 * td + 0.06), 0.1, bs,
                          V.translation((sx * x_pod, cy, 0.0))))
    add('portal.membrane', 'portal_energy',
        P.clipped_disc('portal.membrane', 'portal_energy', centre, ri + 0.03, rs, hd + 0.005))

    # Bridge: structural slab with ramps, a planked walking surface, curbs on both edges.
    path = _deck_path(lay, det['fillet_samples'])
    add('deck.body', 'frame_secondary', P.swept_slab('deck.body', 'frame_secondary', path, W,
                                                     min(0.08, 0.3 * lay['T']), cs))
    if det['details']:
        for name, matrix, half in _planks(lay):
            add('deck.surface', 'bridge_surface',
                P.rounded_box('deck.surface', 'bridge_surface', half, 0.02, 1, matrix))
    else:  # distant LOD: one thin walking layer instead of separate planks
        top = [(z, y + PLANK_PROUD, PLANK_THICKNESS) for z, y, _t in path[1:-1]]
        add('deck.surface', 'bridge_surface',
            P.swept_slab('deck.surface', 'bridge_surface', top, lay['walk'] - 0.02, 0.015, 1))
    ch = p.curb_height
    # Curbs follow the deck path exactly (same bends), so short ramps never fold the sweep.
    curb_path = [(z, y + ch, ch + min(0.06, 0.5 * t)) for z, y, t in path]
    for sx in (1.0, -1.0):
        add('deck.curbs', 'frame_secondary',
            P.swept_slab('deck.curbs', 'frame_secondary', curb_path, p.curb_width, min(0.04, 0.3 * p.curb_width),
                         max(1, cs // 2), x_offset=sx * lay['x_curb']))

    # Rails: posts on the curbs, hand rails through the post tops; they stop short of the ring.
    for sz in (1.0, -1.0):
        z_list = _post_positions(p, lay)
        for sx in (1.0, -1.0):
            x = sx * lay['x_curb']
            tops = []
            for z in z_list:
                y0 = surface_y(lay, z) + ch - 0.01
                y1 = surface_y(lay, z) + p.rail_height
                add('rails.posts', 'frame_secondary',
                    P.post('rails.posts', 'frame_secondary', (x, y0, sz * z), y1 - y0, POST_RADIUS, max(8, ns // 2), 1))
                tops.append((x, y1, sz * z))
                if det['details']:
                    add('rails.knobs', 'accent',
                        P.sphere('rails.knobs', 'accent', (x, y1, sz * z), KNOB_RADIUS, max(8, ns // 2)))
            add('rails.handrails', 'frame_primary',
                P.rail('rails.handrails', 'frame_primary', tops, HANDRAIL_RADIUS, max(8, ns // 2), max(1, cs // 2)))

    # Base: pedestals with accent caps, a low foundation joining them under the deck.
    x_ped = lay['x_ped']
    for sx in (1.0, -1.0):
        base, cap = P.pedestal('base.pedestal', 'frame_secondary', 'accent', (sx * x_ped, 0.0),
                               (0.5 * p.pedestal_width, 0.5 * p.pedestal_height, 0.5 * p.pedestal_depth),
                               min(0.14, 0.2 * p.pedestal_width), bs, cap_thickness=PED_CAP,
                               plinth_height=PLINTH_FRACTION * p.pedestal_height, plinth_grow=PLINTH_GROW)
        add('base.pedestals', 'frame_secondary', base)
        add('base.caps', 'accent', cap)
    ft = lay['foundation_top']
    if ft > 0.05:
        fx = x_ped + 0.5 * p.pedestal_width - 0.05
        add('base.foundation', 'frame_secondary',
            P.rounded_box('base.foundation', 'frame_secondary', (fx, 0.5 * ft, 0.5 * p.pedestal_depth - 0.08),
                          min(0.08, 0.45 * ft), bs, V.translation((0.0, 0.5 * ft, 0.0))))

    if det['details']:
        for sz in (1.0, -1.0):  # bezels around both visible ends of the core
            add('core.bezels', 'accent', P.ring_arc('core.bezels', 'accent', cr * 0.98, 0.06, 0.08, 0.02,
                                                    max(16, ns), max(1, cs // 2), center=(0.0, ky, sz * kd)))
            for sx in (1.0, -1.0):  # pod light bars on both faces
                add('ring.pod_lights', 'emissive_trim',
                    P.capsule('ring.pod_lights', 'emissive_trim', (sx * x_pod, cy - 0.2, sz * (0.5 * td + 0.06)),
                              (sx * x_pod, cy + 0.2, sz * (0.5 * td + 0.06)), 0.045, max(8, ns // 2), 2))
            zt = sz * (0.5 * td + COLLAR_DEPTH + 0.18)  # threshold light bars on both approaches
            add('deck.lights', 'emissive_trim',
                P.rounded_box('deck.lights', 'emissive_trim', (0.5 * lay['walk'] - 0.15, 0.012, 0.035), 0.01, 1,
                              V.translation((0.0, hd + 0.004, zt))))
        for sx in (1.0, -1.0):  # guide lines along the deck
            add('deck.lights', 'emissive_trim',
                P.rounded_box('deck.lights', 'emissive_trim', (0.025, 0.012, zc - 0.15), 0.01, 1,
                              V.translation((sx * (0.5 * lay['walk'] - 0.14), hd + 0.004, 0.0))))

    out = []
    for name in sorted(parts):
        material, pieces = parts[name]
        out.append(M.rounded_part(M.merge(pieces, name=name, material=material)))
    return out


def _planks(lay):
    """``(name, matrix, half_extents)`` for walking planks on the flat deck and both ramps."""
    out = []
    hw, ht = 0.5 * lay['walk'] - 0.01, 0.5 * PLANK_THICKNESS
    zc, hd, s = lay['zc'], lay['hd'], lay['slope']
    n = max(1, int(round(2.0 * zc / PLANK_LENGTH)))
    pitch = 2.0 * zc / n
    for k in range(n):
        z = -zc + pitch * (k + 0.5)
        out.append(('plank', V.translation((0.0, hd + PLANK_PROUD - ht, z)), (hw, ht, 0.5 * (pitch - PLANK_GAP))))
    run = lay['ramp_len'] / math.cos(s) - 0.12
    m = max(1, int(round(run / PLANK_LENGTH)))
    pitch = run / m
    for sign in (1.0, -1.0):
        down = (0.0, -math.sin(s), sign * math.cos(s))
        up = (0.0, math.cos(s), sign * math.sin(s))
        az = V.cross(V.X, up)
        for k in range(m):
            c = V.madd(V.madd((0.0, hd, sign * zc), down, pitch * (k + 0.5)), up, PLANK_PROUD - ht)
            out.append(('plank', V.from_axes(V.X, up, az, c), (hw, ht, 0.5 * (pitch - PLANK_GAP))))
    return out


def _post_positions(p, lay):
    z0, z1 = lay['z_gap'], lay['z_far']
    if z1 <= z0 + 0.3:
        return [z0]
    n = max(2, int(math.ceil((z1 - z0) / p.rail_post_spacing)) + 1)
    return [z0 + (z1 - z0) * k / (n - 1) for k in range(n)]


# --- collision, trigger, sockets ----------------------------------------------------------------

def _ramp_box(name, lay, sign, x, half_x, o0, o1, z0, z1):
    s = lay['slope']
    n = (0.0, math.cos(s), sign * math.sin(s))
    p0 = (x, surface_y(lay, z0), sign * z0)
    p1 = (x, surface_y(lay, z1), sign * z1)
    centre = V.madd(V.lerp(p0, p1, 0.5), n, 0.5 * (o0 + o1))
    az = V.cross(V.X, n)
    return B.make_box(name, centre, (half_x, 0.5 * (o1 - o0), 0.5 * V.length(V.sub(p1, p0))), (V.X, n, az))


def build_collision(p, lay):
    """Simplified collision proxy: oriented boxes only, none across the walk-through opening."""
    hd, T, W, zc, ztoe = lay['hd'], lay['T'], lay['W'], lay['zc'], lay['ztoe']
    ch, cw = p.curb_height, p.curb_width
    boxes = [B.make_box('deck', (0.0, hd - 0.5 * T, 0.0), (0.5 * W, 0.5 * T, zc))]
    rail_top = p.rail_height + KNOB_RADIUS
    rw = KNOB_RADIUS + 0.01
    for sign, side_z in ((1.0, 'front'), (-1.0, 'back')):
        boxes.append(_ramp_box(f'ramp_{side_z}', lay, sign, 0.0, 0.5 * W, -T, 0.0, zc, ztoe))
        for sx, side_x in ((-1.0, 'left'), (1.0, 'right')):
            x = sx * lay['x_curb']
            boxes.append(_ramp_box(f'curb_{side_z}_{side_x}', lay, sign, x, 0.5 * cw, 0.0, ch, zc, ztoe))
            if lay['z_gap'] < zc:
                zm = 0.5 * (lay['z_gap'] + zc)
                boxes.append(B.make_box(f'rail_{side_z}_{side_x}', (x, hd + 0.5 * (ch + rail_top), sign * zm),
                                        (rw, 0.5 * (rail_top - ch), 0.5 * (zc - lay['z_gap']) + rw)))
            boxes.append(_ramp_box(f'rail_ramp_{side_z}_{side_x}', lay, sign, x, rw, ch, rail_top,
                                   max(zc, lay['z_gap']), lay['z_far'] + rw))
    for sx, side_x in ((-1.0, 'left'), (1.0, 'right')):
        boxes.append(B.make_box(f'curb_{side_x}', (sx * lay['x_curb'], hd + 0.5 * ch, 0.0), (0.5 * cw, 0.5 * ch, zc)))
        boxes.append(B.make_box(f'pedestal_{side_x}', (sx * lay['x_ped'], 0.5 * lay['ped_top'], 0.0),
                                (0.5 * p.pedestal_width, 0.5 * lay['ped_top'], 0.5 * p.pedestal_depth)))
        plinth_h = PLINTH_FRACTION * p.pedestal_height
        boxes.append(B.make_box(f'plinth_{side_x}', (sx * (lay['x_ped'] + 0.5 * PLINTH_GROW), 0.5 * plinth_h, 0.0),
                                (0.5 * (p.pedestal_width + PLINTH_GROW), 0.5 * plinth_h,
                                 0.5 * p.pedestal_depth + PLINTH_GROW)))
        boxes.append(B.make_box(f'pod_{side_x}', (sx * lay['x_pod'], lay['cy'], 0.0),
                                (0.2, 0.42, 0.5 * lay['td'] + 0.11)))
    if lay['foundation_top'] > 0.05:
        fx = lay['x_ped'] + 0.5 * p.pedestal_width
        boxes.append(B.make_box('foundation', (0.0, 0.5 * lay['foundation_top'], 0.0),
                                (fx, 0.5 * lay['foundation_top'], 0.5 * p.pedestal_depth)))
    kz = max(lay['kd'], lay['kd'] + 0.6 * p.core_radius)
    boxes.append(B.make_box('keystone', (0.0, lay['ky'], 0.0), (lay['kw'], lay['kh'], kz + 0.04)))
    # Ring: tangent boxes enclosing each arc piece above the pedestals (outside the opening).
    a0 = lay['a_top'] - 0.05
    a1 = math.pi - a0
    n = COLLISION_RING_BOXES
    step = (a1 - a0) / n
    r_in = lay['reff'] * math.cos(0.5 * step)
    r_out = lay['ro'] + COLLAR_OUT
    for k in range(n):
        a = a0 + step * (k + 0.5)
        er = (math.cos(a), math.sin(a), 0.0)
        tangent = (-math.sin(a), math.cos(a), 0.0)
        centre = V.madd((0.0, lay['cy'], 0.0), er, 0.5 * (r_in + r_out))
        boxes.append(B.make_box(f'ring_{k:02d}', centre,
                                (0.5 * (r_out - r_in), r_out * math.sin(0.5 * step) + 0.01,
                                 0.5 * lay['td'] + COLLAR_DEPTH),
                                (er, tangent, V.Z)))
    return boxes


def build_trigger(lay):
    hd, top = lay['hd'], lay['passage_top']
    box = B.make_box('portal_trigger', (0.0, 0.5 * (hd + top), 0.0),
                     (0.5 * lay['passage_w'], 0.5 * (top - hd), 0.5 * lay['td']))
    box.update({'front_normal': [0.0, 0.0, 1.0], 'back_normal': [0.0, 0.0, -1.0],
                'purpose': 'request a realm transition when the player crosses the opening',
                'crossing': 'front->back when entering through +Z, back->front when entering through -Z'})
    return box


def _socket(position, forward, up=V.Y):
    f, u = V.normalize(forward), V.normalize(up)
    x = V.cross(u, f)
    return {'position': list(V.rounded(position)), 'forward': list(V.rounded(f)), 'up': list(V.rounded(u)),
            'rotation': list(V.rounded(V.quat_from_axes(x, u, f)))}


def build_sockets(p, lay):
    zs = lay['z_spawn']
    ys = surface_y(lay, zs)
    sign_y = (0.5 + 0.5 * PLINTH_FRACTION) * p.pedestal_height
    sign_z = 0.5 * p.pedestal_depth + 0.02
    xp = lay['x_ped']
    back = (0.0, 0.0, -1.0)
    return {
        'socket.energy_core': _socket((0.0, lay['ky'], 0.0), V.Z),
        'socket.vfx_center': _socket((0.0, lay['cy'], 0.0), V.Z),
        'socket.spawn_front': _socket((0.0, ys, zs), V.Z),
        'socket.spawn_back': _socket((0.0, ys, -zs), back),
        # left/right as seen by a player approaching that face (front: -X is on their left)
        'socket.sign_left': _socket((-xp, sign_y, sign_z), V.Z),
        'socket.sign_right': _socket((xp, sign_y, sign_z), V.Z),
        'socket.sign_left_back': _socket((xp, sign_y, -sign_z), back),
        'socket.sign_right_back': _socket((-xp, sign_y, -sign_z), back),
    }


# --- validation ---------------------------------------------------------------------------------

def validate_model(model, lay=None):
    """Mesh, material and gameplay checks for a generated model. Never raises."""
    out = []
    p = model.params
    if lay is None:
        lay, _ = compute_layout(p)
    for lod in model.lods:
        subject = f'LOD{lod["level"]}'
        out.extend(M.validate_mesh(lod['parts'], subject=subject, max_extent=MAX_EXTENT))
        for part in lod['parts']:
            if part.material not in SEMANTIC_MATERIALS:
                out.append(_diag('MATERIAL_NOT_SEMANTIC', 'error', 'part uses a non-semantic material',
                                 subject=f'{subject}/{part.name}', material=part.material))
    counts = [M.triangle_count(lod['parts']) for lod in model.lods]
    for i in range(1, len(counts)):
        if counts[i] >= counts[i - 1]:
            out.append(_diag('LOD_NOT_REDUCED', 'warning', 'LOD does not reduce the triangle count',
                             subject=f'LOD{i}', triangles=counts[i], previous=counts[i - 1]))
    trig = model.trigger
    for corner in B.corners(trig):
        x, y, z = corner
        if (x * x + (y - lay['cy']) ** 2 > lay['reff'] ** 2 + 1e-6 or y < lay['hd'] - 1e-6 or
                abs(z) > 0.5 * lay['td'] + 1e-6):
            out.append(_diag('TRIGGER_OUTSIDE_OPENING', 'error', 'trigger volume leaves the ring opening',
                             subject='portal_trigger', corner=corner))
            break
    probe = B.shrunk(trig, 0.005)
    for box in model.collision:
        if B.overlap(probe, box):
            out.append(_diag('COLLISION_BLOCKS_OPENING', 'error',
                             'a collision piece intrudes into the walk-through opening', subject=box['name']))
    if model.lods:
        vb = M.bounds(model.lods[0]['parts'])
        cb = B.aabb(model.collision)
        if any(cb[0][k] > vb[0][k] + COLLISION_TOLERANCE or cb[1][k] < vb[1][k] - COLLISION_TOLERANCE
               for k in range(3)):
            out.append(_diag('COLLISION_BOUNDS_MISMATCH', 'error', 'collision bounds do not enclose the visual bounds',
                             subject='collision', visual=vb, collision=cb, tolerance=COLLISION_TOLERANCE))
    unsupported = []
    half = 0.5 * lay['walk'] - 0.1
    for k in range(41):
        z = -lay['ztoe'] + 0.05 + (2 * lay['ztoe'] - 0.1) * k / 40
        for x in (-half, 0.0, half):
            pt = (x, surface_y(lay, z) - 0.03, z)
            if not any(B.contains_point(b, pt, 0.02) for b in model.collision):
                unsupported.append(V.rounded(pt, 3))
    if unsupported:
        out.append(_diag('DECK_NOT_SUPPORTED', 'error', 'walkable deck surface has no collision underneath',
                         subject='collision', count=len(unsupported), examples=unsupported[:5]))
    missing = [s for s in REQUIRED_SOCKETS if s not in model.sockets]
    if missing:
        out.append(_diag('SOCKET_MISSING', 'error', 'required socket missing', subject='sockets', missing=missing))
    for name in ('socket.spawn_front', 'socket.spawn_back'):
        if name in model.sockets and B.contains_point(trig, tuple(model.sockets[name]['position']), SPAWN_CLEARANCE):
            out.append(_diag('SPAWN_TOO_CLOSE', 'error', 'spawn point would immediately re-enter the trigger',
                             subject=name))
    return out


# --- entry point --------------------------------------------------------------------------------

def generate_portal(params=None):
    """Build every LOD, collision proxy, trigger, sockets and metadata. Never raises."""
    if params is None:
        params = PortalParams()
    if isinstance(params, dict):
        p, diags = params_from_dict(params)
    elif isinstance(params, PortalParams):
        p, diags = params, validate_params(params)
    else:
        p, diags = None, [_diag('PARAMS_INVALID', 'error', 'expected PortalParams or a JSON object', subject='params')]
    if p is None or D.has_errors(diags):
        return PortalModel(diagnostics=D.sort(diags))
    model = PortalModel(params=p, params_hash=params_hash(p))
    try:
        lay, contract = compute_layout(p)
        diags = diags + contract
        if D.has_errors(diags):
            model.diagnostics = D.sort(diags)
            return model
        for spec in LOD_LEVELS[:p.lod_count]:
            det = _detail(p, spec)
            model.lods.append({'level': spec['level'], 'detail': det, 'parts': _build_lod(p, lay, det)})
        model.collision = build_collision(p, lay)
        model.trigger = build_trigger(lay)
        model.sockets = build_sockets(p, lay)
        diags = diags + validate_model(model, lay)
        model.metadata = build_metadata(model, lay)
    except (ArithmeticError, ValueError, KeyError, IndexError) as exc:  # defensive: report, never crash
        model.lods = []
        diags = diags + [_diag('GENERATION_FAILED', 'error', 'geometry generation failed', subject='portal',
                               error=f'{type(exc).__name__}: {exc}')]
    model.diagnostics = D.sort(diags)
    return model


def _bounds_doc(box):
    return {'min': list(V.rounded(box[0])), 'max': list(V.rounded(box[1]))}


def build_metadata(model, lay):
    p = model.params
    lods = []
    for lod in model.lods:
        parts = lod['parts']
        lods.append({'level': lod['level'], 'triangles': M.triangle_count(parts), 'vertices': M.vertex_count(parts),
                     'bounds': _bounds_doc(M.bounds(parts)), 'detail': lod['detail'],
                     'parts': [{'name': part.name, 'material': part.material, 'triangles': part.triangle_count,
                                'vertices': part.vertex_count} for part in parts]})
    used = sorted({part.material for lod in model.lods for part in lod['parts']})
    r6 = lambda v: round(v, 6) + 0.0  # noqa: E731
    return {
        'schema': SCHEMA, 'schema_version': SCHEMA_VERSION,
        'generator': GENERATOR, 'generator_version': GENERATOR_VERSION,
        'variant': p.variant, 'palette': p.palette, 'params_hash': model.params_hash,
        'conventions': dict(CONVENTIONS),
        'bounds': lods[0]['bounds'],
        'opening': {'center': list(V.rounded((0.0, lay['cy'], 0.0))), 'inner_radius': r6(lay['ri']),
                    'effective_radius': r6(lay['reff']), 'clear_width': r6(lay['clear_w']),
                    'clear_height': r6(lay['clear_h']), 'clear_width_measured_over_height': r6(p.min_clear_height),
                    'passage_width': r6(lay['passage_w']), 'passage_height': r6(lay['passage_h'])},
        'bridge': {'walkable_width': r6(lay['walk']), 'deck_top_y': r6(lay['hd']), 'deck_length': r6(lay['L']),
                   'ramp_slope_deg': r6(p.ramp_slope_deg), 'ramp_length': r6(lay['ramp_len']),
                   'total_length': r6(2 * lay['ztoe'])},
        'contract': {'min_clear_width': p.min_clear_width, 'min_clear_height': p.min_clear_height,
                     'min_walkable_width': p.min_walkable_width, 'max_ramp_slope_deg': p.max_ramp_slope_deg},
        'triangle_counts': [lod['triangles'] for lod in lods],
        'lods': lods,
        'materials': used,
        'semantic_materials': list(SEMANTIC_MATERIALS),
        'evidence': VISUAL_EVIDENCE,
        'evidence_detail': {
            'geometry_and_contract_checks': 'UNIT_TESTED',
            'glb_structure_self_check': 'UNIT_TESTED',
            'blender_import': VISUAL_EVIDENCE,
            'unity_import': VISUAL_EVIDENCE,
            'in_game_appearance_and_collision': VISUAL_EVIDENCE,
        },
    }
