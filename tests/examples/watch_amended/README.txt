In this example, a step is created whose amended input is deleted,
after which rerunning should not schedule the job.
Then the file is recreated the same way, which should replay the recorded step.
Then the file is changed, which should rerun the job.
