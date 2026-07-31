static("data/"), static("data/*/") and static("data/*") in one plan all declare the
same directory tree. The same-creator no-op (commit 1) is what lets this succeed
instead of raising "Static tree is a subdirectory of an existing static tree".
