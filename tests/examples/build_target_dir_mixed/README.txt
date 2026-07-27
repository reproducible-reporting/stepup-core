`stepup build` accepts exact-file targets and directory targets together in the same
invocation. This example combines both (`out/ solo.txt exact_optional.txt`) and shows the
asymmetry between them: an exact target still reaches a declared-OPTIONAL step
(`exact_optional.txt`), but a directory target only reaches declared-DEFAULT steps, so
`out/optional.txt` -- OPTIONAL, even though it lives under the targeted directory -- stays
unbuilt.
