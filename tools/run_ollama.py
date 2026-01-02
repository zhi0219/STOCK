import argparse, subprocess, sys
from pathlib import Path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--out-file", required=True)
    args = ap.parse_args()

    prompt_path = Path(args.prompt_file).resolve()
    out_path = Path(args.out_file).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    prompt = prompt_path.read_text(encoding="utf-8", errors="strict")

    # Pass prompt as a single argument to avoid stdin/TTY weirdness
    r = subprocess.run(
        ["ollama", "run", args.model, prompt],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    # Write stdout to file (UTF-8, LF)
    text = (r.stdout or "").replace("\r\n", "\n").replace("\r", "\n")
    out_path.write_text(text, encoding="utf-8", newline="\n")

    # Fail-closed: if ollama exits non-zero, bubble up
    if r.returncode != 0:
        sys.stderr.write(r.stderr or "")
        return r.returncode

    return 0

if __name__ == "__main__":
    raise SystemExit(main())