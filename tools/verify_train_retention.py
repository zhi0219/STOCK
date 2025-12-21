from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.train_daemon import RUNS_ROOT, _retention_sweep


def _make_file(path: Path, size_mb: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"0" * size_mb * 1024 * 1024)


def _create_fake_run(base: Path, name: str, days_ago: int, size_mb: int) -> Path:
    now = datetime.now(timezone.utc)
    run_dir = base / "20240101" / name
    run_dir.mkdir(parents=True, exist_ok=True)
    _make_file(run_dir / "artifact.bin", size_mb)
    past_ts = now - timedelta(days=days_ago)
    mod_time = past_ts.timestamp()
    os.utime(run_dir, (mod_time, mod_time))
    return run_dir


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    test_root = RUNS_ROOT / "_retention_test"
    trap_dir = RUNS_ROOT.parent / "train_runs_trap"
    shutil.rmtree(test_root, ignore_errors=True)
    trap_dir.mkdir(parents=True, exist_ok=True)
    trap_file = trap_dir / "do_not_delete.txt"
    trap_file.write_text("keep", encoding="utf-8")

    try:
        run_old = _create_fake_run(test_root, "run_old", days_ago=10, size_mb=50)
        run_mid = _create_fake_run(test_root, "run_mid", days_ago=5, size_mb=70)
        run_newer = _create_fake_run(test_root, "run_newer", days_ago=1, size_mb=60)
        run_newest = _create_fake_run(test_root, "run_newest", days_ago=0, size_mb=40)

        result = _retention_sweep(
            test_root,
            retain_days=3,
            retain_latest_n=3,
            max_total_train_runs_mb=120,
            dry_run=False,
        )

        kept_paths = {p.resolve() for p in (test_root / "20240101").iterdir() if p.exists()}
        _assert(run_old.resolve() not in kept_paths, "run_old should be deleted")
        _assert(run_mid.resolve() not in kept_paths, "run_mid should be deleted")
        _assert(run_newer.resolve() in kept_paths, "run_newer should be kept")
        _assert(run_newest.resolve() in kept_paths, "run_newest should be kept")

        _assert(len(result.deleted_paths) == 2, "Expected two deletions")
        _assert(result.kept == 2, "Expected two runs to remain")
        _assert(result.total_mb <= 120, "Total size limit not respected")

        _assert(trap_file.exists(), "Trap file outside runs_root should remain")
    finally:
        shutil.rmtree(test_root, ignore_errors=True)
        trap_file.unlink(missing_ok=True)
        if not any(trap_dir.iterdir()):
            trap_dir.rmdir()

    print("verify_train_retention: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
