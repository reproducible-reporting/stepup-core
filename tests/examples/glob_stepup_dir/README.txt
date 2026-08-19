glob("*") in the project root would match ".stepup/" if register_nglob did not reject
it explicitly: NamedGlob does not skip dot entries the way the standard library's glob
does. This check is load-bearing, not defensive: no directory-outside-a-tree rule
catches this indirectly.
