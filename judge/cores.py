"""Select physical benchmark cores and leave their SMT siblings idle."""
import os
from pathlib import Path


def cpu_list(value):
    result = []
    for part in value.split(","):
        ends = part.strip().split("-")
        if len(ends) > 2 or not all(x.isdecimal() for x in ends):
            raise ValueError("Invalid CPU list. Use CPU IDs or ranges such as 2,4-6.")
        first, last = int(ends[0]), int(ends[-1])
        if last < first or last > 65535:
            raise ValueError("Invalid CPU range.")
        result.extend(range(first, last + 1))
    return tuple(result)


def plan(settings, available=None, topology=Path("/sys/devices/system/cpu"), *, auto_limit=5):
    available = set(os.sched_getaffinity(0) if available is None else available)
    groups = {}
    for cpu in sorted(available):
        path = topology / f"cpu{cpu}/topology/thread_siblings_list"
        groups[cpu] = frozenset(cpu_list(path.read_text().strip())) & available
    if settings.benchmark_cpus == "auto":
        preferred = sorted(available, key=lambda cpu: (cpu != settings.cpu, cpu))
        distinct = list(dict.fromkeys(groups[cpu] for cpu in preferred))
        count = min(auto_limit, settings.repetitions, max(1, len(distinct) - 1))
        cores = tuple(next(cpu for cpu in preferred if groups[cpu] == group) for group in distinct[:count])
    else:
        cores = cpu_list(settings.benchmark_cpus)
    if not cores or len(cores) > settings.repetitions or not set(cores) <= available:
        raise ValueError("Benchmark CPUs must be available and cannot exceed the repetition count.")
    if len({groups[cpu] for cpu in cores}) != len(cores):
        raise ValueError("Each benchmark CPU must be on a different physical core.")
    housekeeping = available.difference(*[groups[cpu] for cpu in cores])
    if not housekeeping and len(set(groups.values())) > 1:
        raise ValueError("Reserve at least one physical core for compilation and the web service.")
    return cores, tuple(sorted(housekeeping))
