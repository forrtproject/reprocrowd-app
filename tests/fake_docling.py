"""Stand-in for docling: fake_docling.py --to json --output OUTDIR INPUT → OUTDIR/<stem>.json (the fixture)."""
import shutil
import sys
from pathlib import Path

args = sys.argv[1:]
out, inp = Path(args[args.index("--output") + 1]), Path(args[-1])
if "broken" in inp.name:
    sys.exit("docling: cannot read input")
shutil.copy(Path(__file__).parent / "fixtures" / "paper.docling.json", out / f"{inp.stem}.json")
