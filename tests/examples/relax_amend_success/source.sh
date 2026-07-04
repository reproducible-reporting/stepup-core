#!/usr/bin/env bash
# Simulate a slow producer: dispatched concurrently with the consumer (-j 2),
# but only writes its output well after the consumer has already started.
sleep 0.4
echo hello > data.txt
