"""Resolve user-installed CJK fonts; never copy or download font files."""

import os
from pathlib import Path


def resolve_fonts():
    regular = Path(os.environ.get("DHLS_FONT_PATH", "C:/Windows/Fonts/msyh.ttc"))
    bold = Path(os.environ.get("DHLS_FONT_BOLD_PATH", "C:/Windows/Fonts/msyhbd.ttc"))
    for path in (regular, bold):
        if not path.is_file():
            raise FileNotFoundError(
                "Chinese font missing. Set DHLS_FONT_PATH and DHLS_FONT_BOLD_PATH "
                "to installed font files."
            )
    return str(regular), str(bold)
