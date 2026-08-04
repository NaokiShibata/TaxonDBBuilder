"""Public compatibility surface for the TaxonDBBuilder package."""

from types import ModuleType
import sys as _sys

from .models import *
from .console import *
from .logging_utils import *
from .headers import *
from .markers import *
from .config import *
from .fasta import *
from .postprep.length_filter import *
from .postprep.primer_trim import *
from .postprep.duplicates import *
from .ncbi import *
from .bold import *
from .cli import *

from . import bold as _bold_module
from . import cli as _cli_module
from . import ncbi as _ncbi_module
from .postprep import primer_trim as _primer_module


class _CompatibilityModule(ModuleType):
    """Keep legacy monkeypatch targets synchronized with split modules."""

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if name.startswith("_"):
            return
        for target in (_cli_module, _bold_module, _ncbi_module, _primer_module):
            if hasattr(target, name):
                setattr(target, name, value)


_sys.modules[__name__].__class__ = _CompatibilityModule
__all__ = [name for name in globals() if not name.startswith("_")]
