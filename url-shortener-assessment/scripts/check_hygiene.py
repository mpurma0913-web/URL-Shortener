"""Run transparent local static hygiene checks."""
import json
from pathlib import Path

from shortener.validation import hygiene

if __name__ == "__main__":
    report = hygiene(Path(__file__).resolve().parents[1])
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["outcome"] == "PASS" else 1)
