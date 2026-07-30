This test case builds a file with a step, then drops that step from the plan and
declares a static tree over its directory instead, while the file's on-disk content
changes but its mode/mtime/size/inode are kept identical to what they were right after
the build. It checks that the stale build-time hash is not silently trusted: the file
must be re-hashed, and a step consuming it must pick up the new content.
