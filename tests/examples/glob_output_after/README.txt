The reverse order of glob_output_before: glob("*.txt") is registered first, and a step
declaring out.txt as an output afterwards triggers eager check (b) in
_raise_if_glob_match. The message text is identical to glob_output_before's, since the
diagnostic must not depend on which order the two events happen in.
