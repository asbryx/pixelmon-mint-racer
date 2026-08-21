import subprocess
import json
import pytest
from pathlib import Path

SRC_DIR = Path(__file__).parent.parent / "src"

def test_engine_deterministic_solve():
    solver_script = SRC_DIR / "solve_seed.mjs"
    assert solver_script.exists()

    # Test with random test seed
    test_seed = 123456789
    cmd = ["node", str(solver_script), str(test_seed)]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    out = json.loads(res.stdout.strip())

    assert "outcome" in out
    assert out["outcome"] == "PLAYER_WIN"
    assert "actions" in out
    assert len(out["actions"]) > 0
    assert "turns" in out

def test_engine_multiple_seeds():
    solver_script = SRC_DIR / "solve_seed.mjs"
    seeds = [1001, 20260821, 999999999, 4663]
    for seed in seeds:
        cmd = ["node", str(solver_script), str(seed)]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        out = json.loads(res.stdout.strip())
        assert out["outcome"] == "PLAYER_WIN"
