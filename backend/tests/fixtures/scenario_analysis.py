"""Harmless offline fixture calculation. No dependencies, networking or expected-value access."""
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

groups = defaultdict(list)
with open("inputs/data.csv", newline="") as f:
    for row in csv.DictReader(f):
        groups[row["group"]].append(float(row["rt"]))
means = {group: mean(values) for group, values in groups.items()}
Path("outputs").mkdir(exist_ok=True)
with open("outputs/results.json", "w") as f:
    json.dump({"results": [
        {"id": "T001", "value": means["A"], "step": "A02"},
        {"id": "T002", "value": means["A"] - means["B"], "step": "A02"},
        {"id": "T003", "table": [["Group", "M"]] + [[group, value] for group, value in sorted(means.items())], "step": "A02"},
    ]}, f)
print("Computed group means and their difference from inputs/data.csv.")
