Verifies that step output is persisted in the step_outcome table and that the
environment variable STEPUP_MAX_OUTPUT_SIZE truncates oversized output on a UTF-8 byte boundary.
The failing step confirms that output is stored independently of success,
and that its non-zero return code and resource usage are recorded next to the output.
