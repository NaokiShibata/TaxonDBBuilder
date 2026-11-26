# Port rules

Behavior the port must keep exactly, learned from corrections. Every agent prompt rust-anything writes includes these rules. Edit them by hand, or add one with `rust-anything rules --add`.

- Python callbacks that raise must propagate the original PyErr unchanged (store it and re-raise in the binding); never rebuild the exception from its message text.
- Enable blocks must sys.modules.pop the wrapper module before importlib.import_module so a stale wrapper from another checkout cannot shadow the path insert.
- Sequence data crossing into the port is ASCII-decoded like `str(record.seq)`: non-ASCII bytes raise UnicodeDecodeError naming a single byte, never a range.
