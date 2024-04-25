#!/usr/bin/env python
from stepup.core.api import static, step
from path import Path


def plan_tutorial(tutdir: str, out: list[str]):
    """Define static files and a step for running a tutorial example."""
    workdir = Path(tutdir)
    main = workdir / "main.sh"
    plan = workdir / "plan.py"
    static(workdir, main, plan)
    out = [workdir / out_path for out_path in out]
    step("./main.sh", workdir=workdir, inp=[main, plan], out=out)
    return out


static("getting_started/", "advanced_topics/")
tutout = [
    *plan_tutorial("getting_started/first_step/", ["stdout1.txt", "stdout2.txt"]),
    *plan_tutorial("getting_started/dependencies/", ["stdout.txt"]),
    *plan_tutorial("getting_started/static_files/", ["stdout.txt"]),
    *plan_tutorial("getting_started/static_glob/", ["stdout.txt"]),
    *plan_tutorial("getting_started/static_glob_conditional/", ["stdout1.txt", "stdout2.txt"]),
    *plan_tutorial("getting_started/script_single/", ["stdout.txt"]),
    *plan_tutorial("getting_started/script_multiple/", ["stdout.txt"]),
    *plan_tutorial("getting_started/no_rules/", ["stdout.txt"]),
    *plan_tutorial("getting_started/distributed_plans/", ["stdout.txt"]),
    *plan_tutorial("advanced_topics/static_named_glob/", ["stdout.txt"]),
    *plan_tutorial("advanced_topics/static_deferred_glob/", ["stdout.txt"]),
    *plan_tutorial("advanced_topics/optional_steps/", ["stdout.txt"]),
    *plan_tutorial("advanced_topics/blocked_steps/", ["stdout.txt"]),
    *plan_tutorial("advanced_topics/pools/", ["stdout.txt"]),
    *plan_tutorial("advanced_topics/environment_variables/", ["stdout.txt"]),
    *plan_tutorial("advanced_topics/volatile_outputs/", ["stdout.txt"]),
    *plan_tutorial("advanced_topics/amending_steps/", ["stdout.txt"]),
    *plan_tutorial("advanced_topics/variable_substitution/", ["stdout.txt"]),
    *plan_tutorial("advanced_topics/here_and_root/", ["stdout.txt"]),
]

# step("mkdocs build", workdir="../", inp=tutout)
