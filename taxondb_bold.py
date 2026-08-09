"""Compatibility alias for the historical top-level BOLD API module."""

import sys

from taxondbbuilder import bold_api

sys.modules[__name__] = bold_api
