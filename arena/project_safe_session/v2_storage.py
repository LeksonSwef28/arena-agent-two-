"""Internal/test v2 persistence capability; never grants runtime write admission."""
from .storage import ProjectSafeSessionStore
from .storage_formats import V2_STORAGE_FORMAT


class ProjectSafeSessionStoreV2(ProjectSafeSessionStore):
    """Explicit v2 session store. Coordinator/recovery defaults remain v1."""

    _format = V2_STORAGE_FORMAT
