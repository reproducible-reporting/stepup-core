#!/usr/bin/env bash
VERSION=$1

# Extract the relevant range of lines from the release notes.
# Details are skipped, so that we can still revise the changelog
# without having to fix things on GitHub.
sed -n "/## \[${VERSION}\] -/, /## /{ /##/!p }" docs/changelog.md > notes.md

# Add a link to the full changelog.
echo "See [docs/changelog/#v${VERSION}](https://reproducible-reporting.github.io/stepup-reprep/${VERSION}/changelog/#v${VERSION})" for more details. >> notes.md

# Remove leading and trailing empty lines.
# See https://stackoverflow.com/a/7359879
sed -e :a -e '/./,$!d;/^\n*$/{$d;N;};/\n$/ba' -i notes.md
