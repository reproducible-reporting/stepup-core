#!/usr/bin/env python3
# Emit more than max_output_size (64) bytes so the stored copy is truncated,
# while the terminal user interface still receives the full output.
print("B" * 200)
