This a somewhat convoluted example, trying to cause a race condition:

- Step 1 uses inp1, creates out1, nothing amended
- Step 2 uses inp2, uses out1 (amended), creates out2

After the first execution, inp1 and inp2 are modified and StepUp is restarted.
Step 2 should not be scheduled before Step 1 has been re-executed.
