#!/usr/bin/env bash
git clean -qdfX .
export COLUMNS=80
unset STEPUP_ROOT
stepup boot --no-progress -n 1 | sed -f ../../clean_stdout.sed | sed -e 's|/home/.*/stepup-core/||' > stdout.txt
(echo "{% raw %}"; cat email_template.txt; echo "{% endraw %}") > email_include.txt

# INP: plan.py
# INP: email_template.txt
# INP: config.yaml
