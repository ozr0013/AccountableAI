import sys
from pathlib import Path

# the agent is standalone (not in pyproject's package list); make it importable for its tests
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
