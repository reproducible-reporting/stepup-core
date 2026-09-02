---
title: stepup.core.api
description: >-
  Reference for the functions you call in plan.py,
  including static(), glob(), step(), amend(), run(), plan(), copy() and call().
---

<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

You can expect reasonable stability of the API documented here over the future releases of StepUp.
(No hard promises, since StepUp is still very young.)
Other parts of StepUp, not documented here,
may undergo larger changes and are not intended to be API stable.

## Basic API

### ::: stepup.core.api.static

### ::: stepup.core.api.glob

### ::: stepup.core.api.step

### ::: stepup.core.api.amend

### ::: stepup.core.api.hold

### ::: stepup.core.api.get_info

### ::: stepup.core.api.graph

### ::: stepup.core.api.shq

## Composite API

### ::: stepup.core.api.run

### ::: stepup.core.api.plan

### ::: stepup.core.api.copy

### ::: stepup.core.api.getenv

### ::: stepup.core.api.call

### ::: stepup.core.api.script

### ::: stepup.core.api.loadns

### ::: stepup.core.api.dumpns

### ::: stepup.core.api.render_jinja

## Extension API

This part is meant for developers building StepUp extension packages,
not for use in `plan.py` files.
It lives here instead of in [`stepup.core.extapi`](stepup.core.extapi.md),
which builds on this module and therefore cannot be imported by it.

### ::: stepup.core.api.subs_env_vars

### ::: stepup.core.api.EnvSubstitutor
