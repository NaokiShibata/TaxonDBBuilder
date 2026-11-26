# Port rules

Behavior the port must keep exactly, learned from corrections. Every agent prompt rust-anything writes includes these rules. Edit them by hand, or add one with `rust-anything rules --add`.

- Line iteration in the port must stream (BufReader read_until), never read the whole file: the original keeps memory bounded and leaves the partial output written before a mid-file decode error.
- Python's EOF-truncated UnicodeDecodeError reports position 0 of the final buffer; a truncated sequence followed by a newline is invalid continuation byte at the real position.
- DictReader duplicate header keys resolve to the LAST column with that text, and str(row.get(col)) renders a missing short-row field as the string None.
