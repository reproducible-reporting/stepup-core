A directory glob() match with no static tree declared: `glob("sub/*/")`.
Outside a static tree, StepUp has no evidence that the matched directory is source material
rather than a step's build product, so the match set could depend on build progress.
This raises a GraphError naming the offending directory.
