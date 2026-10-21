"""记忆管线基准（不联网、不写真实 brain/）。

用法：
    python tools/memory_bench.py --n 10000
    python tools/memory_bench.py --n 50000 --skip-consolidate
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10000)
    ap.add_argument("--samples", type=int, default=50)
    ap.add_argument("--skip-consolidate", action="store_true")
    args = ap.parse_args(argv)

    import brainkit as bk

    tmp = Path(tempfile.mkdtemp(prefix="whale-mem-bench-"))
    old = bk.BRAIN_DIR
    try:
        bk.set_brain_dir(tmp)
        bk.save_json(tmp / "manifest.json", {"brain_id": "bench", "fingerprint": "x"})
        n = max(100, int(args.n or 10000))
        writes = []
        t0 = time.perf_counter()
        for i in range(n):
            ts = time.perf_counter()
            bk.remember_structured(
                f"基准记忆 {i}：项目 {i % 97} 在 {i % 12} 月的约定与负责人 {i % 31}",
                type=["事实", "偏好", "约定", "项目"][i % 4],
                importance=(i % 5) + 1,
            )
            writes.append((time.perf_counter() - ts) * 1000.0)
        total_write = time.perf_counter() - t0

        queries = [f"项目 {i}" for i in range(max(1, args.samples))]
        search_ms, hit_ms = [], []
        for q in queries:
            ts = time.perf_counter()
            bk.search_memories(q, 5, record_hits=True)
            search_ms.append((time.perf_counter() - ts) * 1000.0)
        for q in queries[:10]:
            ts = time.perf_counter()
            bk.load_memories()
            hit_ms.append((time.perf_counter() - ts) * 1000.0)

        ts = time.perf_counter()
        import brain_api
        ctx = brain_api.brain_context(max_memories=6)
        inject_ms = (time.perf_counter() - ts) * 1000.0

        consolidate_ms = None
        if not args.skip_consolidate:
            ts = time.perf_counter()
            bk.consolidate_memories(min_importance=0, days=0)
            consolidate_ms = (time.perf_counter() - ts) * 1000.0

        def p95(vals):
            if not vals:
                return 0.0
            vals = sorted(vals)
            return vals[min(len(vals) - 1, int(len(vals) * 0.95))]

        print(json.dumps({
            "n": n,
            "write_total_ms": round(total_write * 1000.0, 1),
            "write_avg_ms": round(statistics.mean(writes), 3),
            "write_p95_ms": round(p95(writes), 3),
            "search_avg_ms": round(statistics.mean(search_ms), 3),
            "search_p95_ms": round(p95(search_ms), 3),
            "load_avg_ms": round(statistics.mean(hit_ms), 3),
            "inject_ms": round(inject_ms, 3),
            "inject_chars": len(ctx or ""),
            "consolidate_ms": None if consolidate_ms is None else round(consolidate_ms, 1),
            "hits_pending": bk.hits_pending(),
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        bk.set_brain_dir(old)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
