glob("inp.txt") matches the file but does not declare it static, unlike the pre-Phase-2
behaviour where glob() declared its matches. cat inp.txt > out.txt then has an AWAITED
input with no producer, and the build fails loudly, reporting unavailable inputs,
instead of silently treating inp.txt as static. This is the migration failure mode
described in the phase's breaking change: use static() to declare, not glob().
