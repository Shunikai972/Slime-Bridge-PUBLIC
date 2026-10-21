"""Art direction of the sanctuary island: a verdant moss garden around an ancient portal court.

Pure layout maths on top of the kit (names, bounds, transforms); the SR1 plugin resolves every mesh and material by name from the
running game. Everything is placed in the walkway frame (u = metres along the heading, v = to the right) and is deterministic.

  * portal court   : a flat paved disc, a henge of pillars with a gap toward the bridge, the portal in the middle
  * statues        : spread over three elliptical rings (inner, middle, outer clearings), each on a low plinth and linked to the
                     court by a stone path; hidden species stand in secluded outer clearings framed by rocks and trees
  * flora          : native trees (reef / moss / pogo), flower beds, glowing flowers, mushrooms, mossy rocks and grass, scattered
                     with exclusion distances so nothing grows through a statue, a pillar, a path or the entrance corridor
  * footprints     : every solid thing records a circle; statues are checked against them (nothing may overlap a statue)
"""
from __future__ import annotations

import math
import random

PHASES = ('rise', 'span', 'court', 'flora', 'statue')
STATUE_RADIUS = 2.3             # statue + plinth footprint
MIN_STATUE_GAP = 6.4            # centre to centre, metres


class IslandError(ValueError):
    pass


# --------------------------------------------------------------------------------------------------------------------
# quaternion helpers (x, y, z, w)
# --------------------------------------------------------------------------------------------------------------------
def q_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz]


def q_rotate(q, v):
    x, y, z, w = q
    # v' = v + 2w(q x v) + 2 q x (q x v)
    cx, cy, cz = y * v[2] - z * v[1], z * v[0] - x * v[2], x * v[1] - y * v[0]
    dx, dy, dz = y * cz - z * cy, z * cx - x * cz, x * cy - y * cx
    return [v[0] + 2 * (w * cx + dx), v[1] + 2 * (w * cy + dy), v[2] + 2 * (w * cz + dz)]


def yaw_q(yaw_deg):
    half = math.radians(yaw_deg) / 2
    return [0.0, math.sin(half), 0.0, math.cos(half)]


# --------------------------------------------------------------------------------------------------------------------
# footprints
# --------------------------------------------------------------------------------------------------------------------
class Footprints:
    def __init__(self):
        self.items = []         # (u, v, radius, kind)

    def add(self, u, v, radius, kind):
        self.items.append((u, v, radius, kind))

    def clear(self, u, v, radius, ignore=()):
        """True when a circle at (u, v) overlaps nothing but the kinds in *ignore*."""
        for (ou, ov, orad, kind) in self.items:
            if kind in ignore:
                continue
            if math.hypot(u - ou, v - ov) < radius + orad:
                return False
        return True

    def overlaps(self, u, v, radius, kinds):
        return [(ou, ov, orad, kind) for (ou, ov, orad, kind) in self.items
                if kind in kinds and math.hypot(u - ou, v - ov) < radius + orad]


def segment_distance(pu, pv, au, av, bu, bv):
    dx, dy = bu - au, bv - av
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((pu - au) * dx + (pv - av) * dy) / length2))
    return math.hypot(pu - (au + t * dx), pv - (av + t * dy))


# --------------------------------------------------------------------------------------------------------------------
# statue slots
# --------------------------------------------------------------------------------------------------------------------
def statue_slots(total, r_in, r_out, forward_squash, rng, gap=MIN_STATUE_GAP):
    """[(du, dv)] offsets from the plaza centre, well spread over the garden (Mitchell best-candidate sampling).

    Angle 0 points away from the bridge. The bridge side stays open so the entrance corridor never meets a statue.
    """
    def allowed(du, dv):
        radius = math.hypot(du / forward_squash, dv)
        angle = abs(math.degrees(math.atan2(dv, du / forward_squash)))        # 0 = ahead, 180 = toward the bridge
        return math.hypot(du, dv) >= r_in and radius <= r_out and angle <= (180.0 - (52.0 if radius < (r_in + r_out) / 2 else 38.0))

    def candidate():
        for _ in range(200):
            radius = math.sqrt(rng.uniform(r_in * r_in, r_out * r_out))
            angle = rng.uniform(-math.pi, math.pi)
            du, dv = radius * math.cos(angle) * forward_squash, radius * math.sin(angle)
            if allowed(du, dv):
                return du, dv
        raise IslandError('no free spot for a statue')

    points = [candidate()]
    while len(points) < total:
        best, best_distance = None, -1.0
        for _ in range(90):
            du, dv = candidate()
            nearest = min(math.hypot(du - a, dv - b) for a, b in points)
            if nearest > best_distance:
                best, best_distance = (du, dv), nearest
        points.append(best)
    return points


def check_statue_spacing(points):
    for i, a in enumerate(points):
        for b in points[i + 1:]:
            if math.hypot(a[0] - b[0], a[1] - b[1]) < MIN_STATUE_GAP:
                raise IslandError('statues are closer than %.1f m' % MIN_STATUE_GAP)


# --------------------------------------------------------------------------------------------------------------------
# flora
# --------------------------------------------------------------------------------------------------------------------
TREE_KEYS = ('tree.reef', 'tree.pogo')            # tree.moss is only canopies and branches: it reads as green domes
ACCENT_KEYS = ('flowers.bed', 'flower.glow', 'shroom.giant', 'stump.moss', 'grass.cluster')
ROCK_PIECES = ('rockMossFlat01', 'rockMossTall01', 'rockMossBoxy01')
GRASS_PIECES = ('grassThinSprig01_LOD0', 'grassThinSprig02_LOD0', 'grassThinColony01_LOD0', 'grassThinColony02_LOD0')


def compound_by_key(kit):
    return {c['key']: c for c in kit.get('flora') or []}


def compound_radius(compound):
    """Trunk / base footprint radius in metres of a compound at scale 1 (largest horizontal half-extent of its lowest part)."""
    low = min(compound['nodes'], key=lambda n: n['local']['position'][1])
    sx = low['local']['scale']
    ext = low['bounds']['extent'] if low.get('bounds') else [1.0, 1.0, 1.0]
    return max(0.6, min(3.0, max(ext[0] * sx[0], ext[2] * sx[2]) * 1.05))
