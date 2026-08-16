"""
Configure Django before any test module is imported.

Importing `regional_packs` is what does it (see its `__init__.py`), and it has to happen before
anything reaches into `backyardchirps`, whose modules read settings as they load. A test module
cannot guarantee that on its own: an import sorter is free to put `backyardchirps` above
`regional_packs` in its own import block, and does. conftest is imported first, so this is the
one place the order is ours to decide.
"""

import regional_packs  # noqa: F401
