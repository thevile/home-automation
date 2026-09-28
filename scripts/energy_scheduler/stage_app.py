"""Build an isolated local-app context from the canonical source files."""
import argparse
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="New, nonexistent directory")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    args.destination.mkdir(parents=True, exist_ok=False)
    shutil.copytree(root / "scripts/energy_scheduler", args.destination / "energy_scheduler",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("config.yaml", "Dockerfile"):
        shutil.copy2(root / "home-assistant/apps/energy_shadow" / name, args.destination / name)
    shutil.copy2(root / "home-assistant/energy_scheduler/settings.json", args.destination / "settings.json")
    print(args.destination)


if __name__ == "__main__":
    main()
