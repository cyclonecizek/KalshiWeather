"""Standard-library-only guard for the static Pages artifact."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def check(root=ROOT):
    for name in ("index.html", "assets/app.js", "assets/math.js", "assets/decision.js", "assets/research.js", "assets/app.css"):
        if not (root / "docs" / name).is_file():
            raise ValueError(f"Missing site asset: {name}")
    # During the first rollout the low board is built after the code deploy.
    # Once present it is validated too; the live smoke test requires it.
    names = ["board.json", "board_temp.json"]
    if (root / "docs/data/board_low.json").exists():names.append("board_low.json")
    for name in names:
        path = root / "docs/data" / name
        board = json.loads(path.read_text())
        if board.get("schema_version") != 2 or not board.get("cities"):
            raise ValueError(f"{name}: a nonempty version-2 board is required")
        if not board.get("generated_at") or not board.get("snapshot_id"):
            raise ValueError(f"{name}: missing issuance metadata")
        json.dumps(board, allow_nan=False)
    print("Publication artifact contains both version-2 boards and required assets")


if __name__ == "__main__":
    check()
