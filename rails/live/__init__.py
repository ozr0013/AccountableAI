"""The live loop: the system reacts to each new fact by itself.

Imported on demand (it uses the workflow package), so `import rails` stays light.

  system.py      LiveSystem: the demo's live loop on RealRails (behind rails/console/api.py)
  sim_system.py  SimSystem: the streaming simulator's adapter around the same Workflow
  sim.py         forward-only, explicitly simulated streaming runner (messy world, comparisons)
"""
