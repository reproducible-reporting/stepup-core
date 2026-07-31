static("sub/foo.txt") declares a file without a static tree, and glob("*/") then
matches the directory "sub/". A directory match no longer has to lie inside a static
tree: it is justified here simply by being the parent of a static file.
