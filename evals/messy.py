"""The same world, but the system only sees what the parser could read from the raw files.

    w2, res = messy_world(generate(13, ALL_SCENARIOS, 30))

Documents are replaced by the parser's output (ids like raw:inbox/...). Ground truth, agreement
terms and the vendor master are kept: those are what the controller already knows. The one
fact read off a statement (the moment's 'invoice received Mar 28') is re-pointed at the parsed
statement it came from.
"""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
from pathlib import Path

from agents.extract import IngestResult, ingest_folder
from world.mess import render


def messy_world(world, root: Path | None = None):
    tmp = None
    if root is None:
        tmp = root = Path(tempfile.mkdtemp(prefix="raw-"))
    render(world, root)
    manifest = json.loads((Path(root) / "manifest.json").read_text())
    res: IngestResult = ingest_folder(root, world.vendors)
    w = copy.deepcopy(world)
    w.documents = list(res.documents)
    # clean doc id -> parsed doc id, via the raw file that carried it
    where = {tid: f["path"] for f in manifest["files"] for tid in f["truth"]}
    for ev in w.facts:
        if ev.source and ev.source.source_id in where:
            path = where[ev.source.source_id]
            match = next((d.id for d in w.documents if d.id.startswith(f"raw:{path}")), None)
            if match:
                ev.source = ev.source.model_copy(update={"source_id": match})
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return w, res
