"""
Thin wrapper — re-exports spawn_execution_job from shared/gke.py.
Kept for backwards compatibility with existing launcher imports.
"""
from shared.gke import spawn_execution_job  # noqa: F401
