`stepup build [targets...]` restricts the build to the steps needed to produce the given
output files. This example walks through the basic use cases: restricting a fresh build to
one output, discovering a target declared only behind a dynamic planning step, completing
the remaining steps with a full untargeted build, and reporting a target that no step ever
produces.
