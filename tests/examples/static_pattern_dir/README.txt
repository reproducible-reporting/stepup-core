static("data/*/") registers each matched directory as a static tree, exactly like a
literal static("data/a/") would. A file inside one of them is lazily declared static
the first time it is used (here, amended as a step input), and no static() call ever
named it directly.
