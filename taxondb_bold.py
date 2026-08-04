"""Compatibility shim for the historical top-level BOLD API module."""

from types import ModuleType
import sys as _sys

from taxondbbuilder import bold_api as _impl

for _name in dir(_impl):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_impl, _name)


class _CompatibilityModule(ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if not name.startswith("__"):
            setattr(_impl, name, value)


_sys.modules[__name__].__class__ = _CompatibilityModule
