# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata


project_root = Path(SPECPATH)

# The compatibility shim imports the package through a wildcard import. Keep
# every package module in the archive so the spec remains correct when a
# module is only reached through that compatibility surface.
package_hiddenimports = collect_submodules("taxondbbuilder")
cogent3_hiddenimports = collect_submodules("cogent3")

# Biopython's format registry and data modules are used through lazy imports
# during GenBank parsing. These imports are required by the --from-gb path.
biopython_hiddenimports = [
    "Bio.Data.IUPACData",
    "Bio.Entrez",
    "Bio.GenBank",
    "Bio.Seq",
    "Bio.SeqFeature",
    "Bio.SeqIO.FastaIO",
    "Bio.SeqIO.InsdcIO",
    "Bio.SeqRecord",
]
typer_rich_hiddenimports = [
    "typer",
    "typer.core",
    "typer.main",
    "typer.rich_utils",
    "rich.console",
    "rich.panel",
    "rich.progress",
    "rich.table",
] + collect_submodules("rich._unicode_data")

# Config files, marker definitions, and primer files are selected at runtime
# by the GUI/CLI, so they must remain external rather than being bundled.
datas = []
# cogent3's citeable dependency reads its own version via importlib.metadata
# at import time, which requires the dist-info to be bundled explicitly.
for _pkg in ("citeable", "cogent3", "scinexus", "piqtree", "kalign-python"):
    datas += copy_metadata(_pkg)

hiddenimports = package_hiddenimports + biopython_hiddenimports
hiddenimports += cogent3_hiddenimports
hiddenimports += typer_rich_hiddenimports

a = Analysis(
    [str(project_root / 'taxondbbuilder.py')],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='taxondbbuilder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
