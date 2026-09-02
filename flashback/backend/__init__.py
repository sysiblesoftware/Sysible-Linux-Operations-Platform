"""Sysible Flashback — the standalone SLOP module.

A self-contained FastAPI service (its own content-addressed version store, agent
ingest, and browse / diff / download / restore) fronted by the SLOP gateway at
/flashback. This is the standalone evolution of the original Sysible D3lorean
Controller plugin (still bundled in this repo as ``sysible_d3lorean`` for direct
Controller integration); Flashback needs no Controller — it stands on its own.
"""
