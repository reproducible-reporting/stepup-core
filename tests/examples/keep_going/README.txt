By default, the scheduler is put on hold after a step fails, so an independent
step that has not started yet does not run either, similar to `make` without `-k`.
The `--keep-going` (`-k`) flag restores the old behavior of continuing to build
every step whose inputs remain available, similar to `make -k`.
