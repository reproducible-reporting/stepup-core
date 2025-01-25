This example tests whether nested steps are kept pending correctly:

1. First, a set of nested steps is created, which execute normally.
2. Then the input of the first is removed, and the input of the last is changed.
   Upon restart, no steps should run.
3. Finally, the input of the first is restored in its original form.
   The first step should be skipped, and the last should rerun with the new input.
