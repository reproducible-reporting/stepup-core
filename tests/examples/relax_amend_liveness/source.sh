#!/usr/bin/env bash
# Finishes well before the consumer calls amend(), but after the consumer started
# (both are dispatched concurrently under -j 2, with no declared dependency yet).
sleep 0.4
echo hello > data.txt
