/^$/d
s|Listening on .*/stepup-[^/]*/director|Listening on /tmp/stepup-########/director|
s/\(StepUp Core [0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)[^)]*/\1/
/DIRECTOR │ Wall/d
