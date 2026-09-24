"""Syncwave 🌊

Turn plain JSON files into a live data store, two-way synced with your Python objects.

Imports available from this module:

- [Syncwave](https://syncwave.dev/api/syncwave/#syncwave.Syncwave)
- [SyncCollection](https://syncwave.dev/api/sync_collection/#syncwave.SyncCollection)
- [SyncDict](https://syncwave.dev/api/sync_collections/#syncwave.SyncDict)
- [SyncList](https://syncwave.dev/api/sync_collections/#syncwave.SyncList)
- [SyncSet](https://syncwave.dev/api/sync_collections/#syncwave.SyncSet)
- [SyncModel](https://syncwave.dev/api/sync_model/#syncwave.SyncModel)
- [SyncRoot](https://syncwave.dev/api/sync_model/#syncwave.SyncRoot)
- [Reactive](https://syncwave.dev/api/reactive/#syncwave.Reactive)
- [SyncState](https://syncwave.dev/api/reactive/#syncwave.SyncState)
- [DeadReferenceError](https://syncwave.dev/api/errors/#syncwave.DeadReferenceError)
"""

__version__ = "0.2.1"


from .errors import DeadReferenceError
from .reactive import Reactive, SyncState
from .sync_collection import SyncCollection, SyncDict, SyncList, SyncSet
from .sync_model import SyncModel, SyncRoot
from .syncwave import Syncwave

__all__ = [
    "DeadReferenceError",
    "Reactive",
    "SyncCollection",
    "SyncDict",
    "SyncList",
    "SyncModel",
    "SyncRoot",
    "SyncSet",
    "SyncState",
    "Syncwave",
]
