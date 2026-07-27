A directory registered with `static()` is a static tree, so its own root path
(`subdir`, without a trailing slash) matches the tree and can be used as a step
input. Hashing it fails, since a directory is not a file: the hash job logs an
`ERROR` and puts the scheduler on hold, so the build phase stops dispatching new
steps and reports the failure clearly, instead of leaving `subdir` silently stuck
`UNCONFIRMED` forever with `cat subdir` never running.
