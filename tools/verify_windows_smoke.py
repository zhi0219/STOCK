import argparse
import platform
import sys
import traceback
from pathlib import Path

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]

def _write_text(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts-dir", required=True)
    args = ap.parse_args()

    repo = _repo_root()
    sys.path.insert(0, str(repo))
    art = Path(args.artifacts_dir).resolve()
    art.mkdir(parents=True, exist_ok=True)
    err_file = art / "windows_smoke.txt"

    print("WINDOWS_SMOKE_START")

    if platform.system().lower() != "windows":
        print("WINDOWS_SMOKE_STEP|name=os_check|status=SKIP")
        print("WINDOWS_SMOKE_SUMMARY|status=SKIP|reason=not_windows|artifacts=%s" % art)
        print("WINDOWS_SMOKE_END")
        return 0

    print("WINDOWS_SMOKE_STEP|name=os_check|status=PASS")
    try:
        import tools.ui_app  # noqa: F401
        print("WINDOWS_SMOKE_STEP|name=import_tools_ui_app|status=PASS")
        print("WINDOWS_SMOKE_SUMMARY|status=PASS|artifacts=%s" % art)
        print("WINDOWS_SMOKE_END")
        return 0
    except Exception:
        tb = traceback.format_exc()
        _write_text(err_file, tb)
        print("WINDOWS_SMOKE_STEP|name=import_tools_ui_app|status=FAIL")
        print("WINDOWS_SMOKE_SUMMARY|status=FAIL|artifacts=%s|err=%s" % (art, err_file))
        print("WINDOWS_SMOKE_END")
        return 2

if __name__ == "__main__":
    raise SystemExit(main())