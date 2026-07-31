A static tree cannot be rooted at the project directory itself.

`static("./")` would make the tree the sole owner of `plan.py` and of every
file any step ever produces, which defeats the point of a static tree.
The plan step fails immediately with a message naming the restriction.
