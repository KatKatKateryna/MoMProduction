"""
Run all update_db_* scripts in sequence.
"""

import importlib
import sys
import traceback

SCRIPTS = [
    "update_db_gfms",
    "update_db_hwrf",
    "update_db_viirs",
    "update_db_dfo",
    "update_db_glofas",
    "update_db_final_alert",
]


def run_all():
    failed = []
    for script in SCRIPTS:
        print(f"\n{'='*60}")
        print(f"  Running {script}")
        print(f"{'='*60}")
        try:
            module = importlib.import_module(script)
            module.main()
        except Exception:
            print(f"  ERROR in {script}:")
            traceback.print_exc()
            failed.append(script)

    print(f"\n{'='*60}")
    if failed:
        print(f"Completed with errors. Failed scripts: {', '.join(failed)}")
        sys.exit(1)
    else:
        print("All scripts completed successfully.")


if __name__ == "__main__":
    run_all()
