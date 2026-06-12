A step that amends its node in the graph with a volatile file,
and then fails to create it, will NOT result in an error.
Volatile files "can" exist and will be cleaned up when needed and if present,
but there are not other expectations about them.
