"""Unity AnimationCurve evaluation (cubic Hermite with in/out slopes, infinite slope = stepped), vectorised."""

import numpy as np

STEP = 1e9


def eval_scalar(keys, t, comp=None):
    """Evaluate one component of a key list at times ``t`` (array). Keys: {'t','v','i','o'} (v/i/o scalar or list)."""
    ts = np.array([k['t'] for k in keys], dtype=float)
    g = (lambda x: x) if comp is None else (lambda x: x[comp])
    v = np.array([g(k['v']) for k in keys], dtype=float)
    i_ = np.array([g(k['i']) for k in keys], dtype=float)
    o_ = np.array([g(k['o']) for k in keys], dtype=float)
    t = np.atleast_1d(np.asarray(t, dtype=float))
    last = len(ts) - 1
    if last == 0:
        return np.full(t.shape, v[0])
    a = np.clip(np.searchsorted(ts, t, side='right') - 1, 0, last)
    b = np.minimum(a + 1, last)
    dt = np.maximum(ts[b] - ts[a], 1e-12)
    s = np.clip((t - ts[a]) / dt, 0.0, 1.0)
    m0, m1 = o_[a], i_[b]
    s2, s3 = s * s, s * s * s
    with np.errstate(invalid='ignore', over='ignore'):
        hermite = (2 * s3 - 3 * s2 + 1) * v[a] + (s3 - 2 * s2 + s) * dt * m0 + (-2 * s3 + 3 * s2) * v[b] + (s3 - s2) * dt * m1
    stepped = (np.abs(m0) >= STEP) | (np.abs(m1) >= STEP) | ~np.isfinite(m0) | ~np.isfinite(m1)
    out = np.where(stepped, v[a], hermite)
    out = np.where(t >= ts[last], v[last], out)
    out = np.where(t <= ts[0], v[0], out)
    return out


def eval_vector(keys, t, width):
    return np.stack([eval_scalar(keys, t, c) for c in range(width)], axis=1)


def sample_times(length, rate, keys=()):
    n = max(2, int(np.ceil(length * rate)) + 1)
    ts = np.linspace(0.0, length, n)
    extra = [k['t'] for k in keys if 0 <= k['t'] <= length]
    return np.unique(np.concatenate([ts, extra])) if extra else ts
