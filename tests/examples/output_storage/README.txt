Verifies that step output is persisted in the step_output table and that the
boot option max_output_size truncates oversized output on a UTF-8 byte boundary.
The failing step confirms that output is stored independently of success.
