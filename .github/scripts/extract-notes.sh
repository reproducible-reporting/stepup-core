#!/usr/bin/env bash
# Usage: .github/scripts/extract-notes.sh OWNER SLUG GITREF

OWNER=${1}
SLUG=${2}
GITREF=${3}

if [[ "${GITREF}" == "refs/tags/"* ]]; then
    TAG="${GITREF#refs/tags/}"
    VERSION="${TAG#v}"
    MAJMIN=${VERSION%.*}
else
    TAG="0.0.0"
    VERSION="Unreleased"
    MAJMIN="0.0"
fi

# Extract the release notes from the changelog
sed -n "/## \[${VERSION}\]/, /## /{ /##/!p }" docs/changelog.md > notes.md

# Add a link to the release notes
URL="https://${OWNER}.github.io/${SLUG}/${MAJMIN}/changelog/#${TAG}"
echo "See [docs/changelog/#${TAG}](${URL}) for more details." >> notes.md

# Remove leading and trailing empty lines
sed -e :a -e '/./,$!d;/^\n*$/{$d;N;};/\n$/ba' -i notes.md
