# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- More graceful error message when director process crashes early.
- Fix compatibility with [asciinema](https://asciinema.org) terminal recording.

### Changed

- Limit acyclic constraint to the supplier-consumer graph.
  This means a step can declare a static file and then amend it as input.


## [1.0.0] - 2024-04-25

Initial release
