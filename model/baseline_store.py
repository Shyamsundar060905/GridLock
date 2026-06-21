"""
Rolling 8-week congestion baseline per (segment, hour-of-week).

ECS needs `baseline_ratio` = the typical live congestion ratio for a segment at a
given hour-of-week, measured ONLY during windows with no active violation. That
baseline can't be backfilled from the historical dataset — it accumulates as the
live poller runs. This store is the accumulator: a JSON-backed exponentially-
decayed mean (8-week half-life) keyed by (segment_id, hour_of_week 0..167).

Used by the live ECS path (api/main.py) and by the pipeline's ECSProvider.
Call `update(segment_id, hour_of_week, live_ratio, has_violation=False)` from the
poller (it ignores samples where a violation is active), then `get(...)` returns
the current baseline (or None until enough samples exist).
"""
import json, os, math

WEEK_HOURS = 168
DECAY_HALFLIFE_SAMPLES = 8 * 7   # ~8 weeks of daily samples per hour-of-week slot
MIN_SAMPLES = 5                  # below this, baseline is considered not-yet-ready


class BaselineStore:
    def __init__(self, path="ecs_baseline.json"):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            try:
                self.data = json.load(open(path, encoding="utf-8"))
            except Exception:
                self.data = {}

    @staticmethod
    def _key(segment_id, hour_of_week):
        return f"{segment_id}@{int(hour_of_week) % WEEK_HOURS}"

    def update(self, segment_id, hour_of_week, live_ratio, has_violation=False):
        """Feed a live sample. No-op when a violation is active (baseline must be
        the clean, no-violation congestion level)."""
        if has_violation or live_ratio is None:
            return
        k = self._key(segment_id, hour_of_week)
        rec = self.data.get(k, {"mean": float(live_ratio), "n": 0})
        # exponential decay: weight recent samples, ~8-week effective window
        alpha = 1 - math.exp(-math.log(2) / DECAY_HALFLIFE_SAMPLES)
        rec["mean"] = (1 - alpha) * rec["mean"] + alpha * float(live_ratio)
        rec["n"] = rec["n"] + 1
        self.data[k] = rec

    def get(self, segment_id, hour_of_week, default=0.0):
        """Current baseline ratio, or `default` until MIN_SAMPLES accumulated."""
        rec = self.data.get(self._key(segment_id, hour_of_week))
        if not rec or rec["n"] < MIN_SAMPLES:
            return default
        return rec["mean"]

    def ready(self, segment_id, hour_of_week):
        rec = self.data.get(self._key(segment_id, hour_of_week))
        return bool(rec and rec["n"] >= MIN_SAMPLES)

    def save(self):
        json.dump(self.data, open(self.path, "w", encoding="utf-8"))

    def stats(self):
        ns = [r["n"] for r in self.data.values()]
        ready = sum(1 for r in self.data.values() if r["n"] >= MIN_SAMPLES)
        return {"segments_hours_tracked": len(self.data), "ready": ready,
                "total_samples": int(sum(ns))}


if __name__ == "__main__":
    # self-test: feed samples, confirm warm-up + decay behaviour
    import tempfile
    p = os.path.join(tempfile.gettempdir(), "ecs_baseline_test.json")
    if os.path.exists(p):
        os.remove(p)
    bs = BaselineStore(p)
    seg, how = "seg1", 10
    assert bs.get(seg, how) == 0.0 and not bs.ready(seg, how), "should start unready"
    for _ in range(MIN_SAMPLES):
        bs.update(seg, how, 0.3)
    assert bs.ready(seg, how), "should be ready after MIN_SAMPLES"
    assert abs(bs.get(seg, how) - 0.3) < 0.05, f"baseline ~0.3, got {bs.get(seg, how)}"
    bs.update(seg, how, 0.9, has_violation=True)         # ignored
    assert abs(bs.get(seg, how) - 0.3) < 0.05, "violation sample must be ignored"
    bs.save()
    print("baseline_store self-test OK:", bs.stats())
