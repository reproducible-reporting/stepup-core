A static tree, `static("src/")`, combined with `glob("src/*.txt")` whose matches are
only printed, never consumed as a step input. This must stay reactive in watch mode:
adding `src/b.txt` is picked up and reruns `./plan.py`, even though nothing under the
tree was ever used as a step input before the change.
