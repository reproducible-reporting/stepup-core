A simple example with an early shutdown where one step remains pending.
This example mainly tests the returncode of StepUp in this scenario.

The timings in main.sh and plan.py are slightly fragile and therefore somewhat exagerated.
StepUp is shut down after 0.5 seconds, while the first step is sleeping for 5 seconds.
