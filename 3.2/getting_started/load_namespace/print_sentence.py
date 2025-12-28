#!/usr/bin/env python3
from stepup.core.api import loadns

config = loadns("config.toml")
print(f"I love {config.fruit} an {config.vegetable} salad.")
