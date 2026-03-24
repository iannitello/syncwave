"""Turn plain JSONs into live data stores, two-way synced with Python objects."""

__version__ = "0.2.1"


from .reactive import DeadReferenceError, Reactive
from .sync_collection import SyncCollection, SyncDict, SyncList, SyncSet
from .sync_model import SyncModel, is_sync_model_supported
from .syncwave import Syncwave

__all__ = [
    "DeadReferenceError",
    "Reactive",
    "SyncCollection",
    "SyncDict",
    "SyncList",
    "SyncModel",
    "SyncSet",
    "Syncwave",
    "is_sync_model_supported",
]
