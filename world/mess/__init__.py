"""Messy paperwork for the clean world: PDFs, scans, emails, bank CSVs.

    python -m world.mess --out out/raw            # render raw files + manifest.json
    from world.mess import render                 # render(world, root) -> Rendered

agents.extract reads these files back into Documents; evals.extraction scores it.
"""

from .render import RawFile, Rendered, render

__all__ = ["RawFile", "Rendered", "render"]
