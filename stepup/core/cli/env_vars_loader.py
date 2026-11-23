# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Load the `stepup.env_vars` entry points and check the variables they register.

Each entry point names a callable that returns the `EnvVarRegistry` of one package,
see `stepup.core.env_vars`.
StepUp Core registers its own variables through the same entry point group,
with `core_env_vars`.

## Problems

Loading never raises.
Anything wrong with an entry point, with what it registers, or with the value of a variable
that is set, is recorded as a `ConfigProblem`, as `ConfigLoader` does for the config files.
A problem that concerns one variable is located at that variable,
so that `stepup config` can show it on the line that lists the variable, when it is set.
A problem that concerns an entry point as a whole is located at the entry point.

## Names

The variables of an extension start with `STEPUP_<NAME>_`,
where `<NAME>` is the name of its entry point, converted as a section name is by `ConfigLoader`.
The variables of StepUp Core are exempt, because their names predate this convention.
The name of an entry point therefore cannot be that of a subcommand of StepUp Core,
whose section already uses that part of the namespace for its settings.
"""

from importlib.metadata import EntryPoint, entry_points

import attrs

from ..converters import boolean
from ..env_vars import EnvVarInfo, EnvVarRegistry
from ..rpc.transport import parse_rpc_transport
from ..utils import parse_path_filter
from ..watch.select import parse_watcher_backend
from ..watch.watchfiles import parse_recursive_watch
from .config_loader import ConfigLoader, ConfigLocation, ConfigProblem

__all__ = ("EnvVarCatalog", "RegisteredEnvVar", "core_env_vars", "load_env_var_catalog")


_ENV_VARS_GROUP = "stepup.env_vars"
"""The entry point group of the callables that return an `EnvVarRegistry`."""

_TOOLS_GROUP = "stepup.tools"
"""The entry point group of the subcommands, whose sections are reserved for StepUp Core."""

_CORE_PACKAGE = "StepUp Core"
"""The package name in the registry of StepUp Core."""


def core_env_vars() -> EnvVarRegistry:
    """Return the variables that StepUp Core acts on without a subcommand defining a setting.

    This registry is maintained by hand:
    nothing derives it from the places that read these variables,
    which `test_env_vars_are_classified` guards against.
    """
    return EnvVarRegistry(
        _CORE_PACKAGE,
        {
            "STEPUP_DEBUG": EnvVarInfo(secret=False, validator=boolean),
            "STEPUP_MAX_OUTPUT_SIZE": EnvVarInfo(secret=False, validator=int),
            "STEPUP_PATH_FILTER": EnvVarInfo(secret=False, validator=parse_path_filter),
            "STEPUP_ROOT": EnvVarInfo(secret=False),
            "STEPUP_RPC_TRANSPORT": EnvVarInfo(secret=False, validator=parse_rpc_transport),
            "STEPUP_SHELL": EnvVarInfo(secret=False),
            "STEPUP_SYNC_RPC_TIMEOUT": EnvVarInfo(secret=False, validator=float),
            "STEPUP_WATCHER": EnvVarInfo(secret=False, validator=parse_watcher_backend),
            "STEPUP_WATCHER_RECURSIVE": EnvVarInfo(secret=False, validator=parse_recursive_watch),
            # StepUp sets the following for the subprocesses it starts.
            "STEPUP_DIRECTOR_SOCKET": EnvVarInfo(secret=False, overridden=True),
            "STEPUP_JOB_I": EnvVarInfo(secret=False, overridden=True),
            "STEPUP_REPORTER_SOCKET": EnvVarInfo(secret=False, overridden=True),
            "STEPUP_STARTUP_I": EnvVarInfo(secret=False, overridden=True),
            "STEPUP_STEP_INP_DIGEST": EnvVarInfo(secret=False, overridden=True),
            "STEPUP_STEP_NEED": EnvVarInfo(secret=False, overridden=True),
        },
    )


#
# Value objects
#


@attrs.define(frozen=True)
class RegisteredEnvVar:
    """An environment variable, as one entry point registered it."""

    info: EnvVarInfo = attrs.field()
    """The description of the variable."""

    package: str = attrs.field()
    """The human readable name of the package that registered the variable."""

    entry_point: str = attrs.field()
    """The name of the entry point that registered the variable."""

    @property
    def owner(self) -> str:
        """The package and its entry point, as they are named in a problem."""
        return f"{self.package} (entry point {self.entry_point!r})"


@attrs.define
class EnvVarCatalog:
    """The variables registered through the `stepup.env_vars` entry points, after checking."""

    packages: list[str] = attrs.field(factory=list)
    """The packages that registered variables, StepUp Core first and the others sorted by name."""

    env_vars: dict[str, RegisteredEnvVar] = attrs.field(factory=dict)
    """The registered variables, keyed by name.

    A name that is also the environment variable of a setting is left out,
    because the setting is what the variable configures.
    A name registered twice keeps its first registration, StepUp Core coming first.
    """

    problems: list[ConfigProblem] = attrs.field(factory=list)
    """Everything wrong with the entry points, their registries and the values that are set."""


#
# Loading
#


def load_env_var_catalog(loader: ConfigLoader) -> EnvVarCatalog:
    """Load every `stepup.env_vars` entry point and check the variables it registers.

    Parameters
    ----------
    loader
        The configuration loader, with every parser patched into it,
        which knows the settings and the environment.

    Returns
    -------
    catalog
        The registered variables, and the problems found while loading and checking them.
    """
    catalog = EnvVarCatalog()
    reserved = {
        _name_infix(ep.name): ep.name for ep in entry_points(group=_TOOLS_GROUP) if _is_core(ep)
    }
    recognized = loader.recognized_env_vars()
    core_packages = set()
    other_packages = set()
    for ep in sorted(entry_points(group=_ENV_VARS_GROUP), key=_entry_point_order):
        registry = _load_registry(ep, catalog.problems)
        if registry is None:
            continue
        (core_packages if _is_core(ep) else other_packages).add(registry.package)
        _register(catalog, ep, registry, reserved, recognized, loader.env_prefix)
    catalog.packages = sorted(core_packages) + sorted(other_packages - core_packages)
    _check_values(catalog, loader.prefixed_env_vars())
    return catalog


def _entry_point_order(ep: EntryPoint) -> tuple[bool, str, str]:
    """Sort entry points with those of StepUp Core first, and the others by name."""
    return (not _is_core(ep), ep.name, ep.value)


def _entry_point_location(ep: EntryPoint) -> ConfigLocation:
    """Locate an entry point, as it is written in the metadata of its package."""
    return ConfigLocation.of_entry_point(f"{ep.name} = {ep.value}")


def _is_core(ep: EntryPoint) -> bool:
    """Whether an entry point belongs to StepUp Core, judged by the module it points to."""
    return ep.module == "stepup.core" or ep.module.startswith("stepup.core.")


def _name_infix(name: str) -> str:
    """Convert the name of an entry point to the part it claims in the names of variables.

    The conversion is that of a section name in `ConfigLoader`,
    so that an entry point and a subcommand of the same name claim the same part.
    """
    return name.upper().replace(".", "_").replace("-", "_")


def _describe(exc: Exception) -> str:
    """Name an exception and its message, for a problem caused by a bug in a package."""
    return f"{type(exc).__name__}: {exc}"


def _load_registry(ep: EntryPoint, problems: list[ConfigProblem]) -> EnvVarRegistry | None:
    """Load an entry point and call it, recording a problem when it does not yield a registry.

    Returns
    -------
    registry
        The registry of the package, `None` when the entry point is broken.
    """
    location = _entry_point_location(ep)
    # The code of another package may fail in any way,
    # which is a problem to report and not a reason to stop reporting the others.
    try:
        func = ep.load()
    except Exception as exc:  # noqa: BLE001
        problems.append(ConfigProblem(f"cannot be loaded: {_describe(exc)}", location))
        return None
    try:
        registry = func()
    except Exception as exc:  # noqa: BLE001
        problems.append(ConfigProblem(f"raised {_describe(exc)}", location))
        return None
    if not isinstance(registry, EnvVarRegistry):
        detail = f"returned {type(registry).__name__} instead of EnvVarRegistry"
        problems.append(ConfigProblem(detail, location))
        return None
    if not isinstance(registry.package, str) or registry.package == "":
        detail = "returned an EnvVarRegistry whose package is not a non-empty str"
        problems.append(ConfigProblem(detail, location))
        return None
    if not isinstance(registry.env_vars, dict) or not all(
        isinstance(name, str) and isinstance(info, EnvVarInfo)
        for name, info in registry.env_vars.items()
    ):
        detail = "returned an EnvVarRegistry whose env_vars is not a dict[str, EnvVarInfo]"
        problems.append(ConfigProblem(detail, location))
        return None
    return registry


def _register(
    catalog: EnvVarCatalog,
    ep: EntryPoint,
    registry: EnvVarRegistry,
    reserved: dict[str, str],
    recognized: set[str],
    env_prefix: str,
) -> None:
    """Add the variables of one registry to the catalog, recording what is wrong with them.

    Parameters
    ----------
    catalog
        The catalog, extended in place.
    ep
        The entry point that returned the registry.
    registry
        The variables of one package.
    reserved
        The subcommands of StepUp Core, keyed by the part of a variable name their section claims.
    recognized
        The names of the environment variables of the settings.
    env_prefix
        The prefix of all variables, e.g. `STEPUP_`.
    """
    is_core = _is_core(ep)
    infix = _name_infix(ep.name)
    if not is_core and infix in reserved:
        detail = f"the name {ep.name!r} is taken by the subcommand 'stepup {reserved[infix]}'"
        catalog.problems.append(ConfigProblem(detail, _entry_point_location(ep)))
    name_prefix = f"{env_prefix}{infix}_"
    for name, info in sorted(registry.env_vars.items()):
        entry = RegisteredEnvVar(info, registry.package, ep.name)
        location = ConfigLocation.of_env(name)
        details = []
        if not (is_core or name.startswith(name_prefix)):
            details.append(f"registered by {entry.owner}, but not prefixed with {name_prefix}")
        if info.overridden and info.validator is not None:
            details.append(
                f"registered by {entry.owner} as overridden by StepUp, "
                "which leaves its validator unused"
            )
        first = catalog.env_vars.get(name)
        if name in recognized:
            details.append(f"registered by {entry.owner}, but it configures a setting")
        elif first is not None:
            details.append(f"registered by {first.owner} and by {entry.owner}")
        else:
            catalog.env_vars[name] = entry
        catalog.problems.extend(ConfigProblem(detail, location) for detail in details)


def _check_values(catalog: EnvVarCatalog, env: dict[str, str]) -> None:
    """Record a problem for every registered variable whose value its validator rejects.

    The value of a secret variable never appears in a problem,
    not even through the message of an exception, which may quote it.

    Parameters
    ----------
    catalog
        The catalog, whose problems are extended in place.
    env
        The environment variables with the prefix, keyed by name.
    """
    for name, value in sorted(env.items()):
        entry = catalog.env_vars.get(name)
        if entry is None or entry.info.overridden or entry.info.validator is None:
            continue
        secret = entry.info.secret
        try:
            entry.info.validator(value)
        except ValueError as exc:
            detail = "invalid value" if secret or str(exc) == "" else str(exc)
        except Exception as exc:  # noqa: BLE001
            reason = type(exc).__name__ if secret else _describe(exc)
            detail = f"bug in {entry.owner}, whose validator raised {reason}"
        else:
            continue
        catalog.problems.append(ConfigProblem(detail, ConfigLocation.of_env(name)))
