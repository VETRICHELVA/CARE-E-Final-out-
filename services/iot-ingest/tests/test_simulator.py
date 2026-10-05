import importlib.util
from itertools import islice
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "simulate_telemetry.py"


def load_simulator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("simulate_telemetry", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def in_normal_range(temps: list[float]) -> bool:
    return all(3.5 <= t <= 5.5 for t in temps)


def test_normal_stays_in_range() -> None:
    temps = list(islice(load_simulator().temperatures("normal", 10), 500))
    assert len(temps) == 500
    assert in_normal_range(temps)


def test_excursion_follows_a_minute_of_normal_readings() -> None:
    sim = load_simulator()
    for interval, warmup in [(10, 6), (2, 30)]:
        temps = list(islice(sim.temperatures("excursion", interval), warmup + 2 + 50))
        assert in_normal_range(temps[:warmup])
        assert temps[warmup : warmup + 2] == [9.1, 9.4]
        assert in_normal_range(temps[warmup + 2 :])


def test_silent_stops_after_a_minute() -> None:
    temps = list(load_simulator().temperatures("silent", 10))
    assert len(temps) == 6
    assert in_normal_range(temps)
