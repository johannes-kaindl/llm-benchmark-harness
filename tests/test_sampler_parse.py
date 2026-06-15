from ramcheck import sampler


def test_parse_pmset_ac():
    assert (
        sampler.parse_pmset_power("Now drawing from 'AC Power'\n -InternalBattery-0 100%") == "ac"
    )


def test_parse_pmset_battery():
    assert sampler.parse_pmset_power("Now drawing from 'Battery Power'") == "battery"


def test_parse_pmset_unknown():
    assert sampler.parse_pmset_power("garbage") == "unknown"


def test_memory_pressure_from_free_percentage():
    assert sampler.parse_memory_pressure("System-wide memory free percentage: 72%") == "normal"
    assert sampler.parse_memory_pressure("System-wide memory free percentage: 20%") == "warn"
    assert sampler.parse_memory_pressure("System-wide memory free percentage: 4%") == "critical"


def test_memory_pressure_explicit_level_wins():
    assert sampler.parse_memory_pressure("status: CRITICAL\nfree percentage: 80%") == "critical"
    assert sampler.parse_memory_pressure("level: warning") == "warn"


def test_powermetrics_throttle_literal():
    assert sampler.parse_powermetrics_throttle("CPU Throttle: yes") is True
    assert sampler.parse_powermetrics_throttle("CPU Throttle: no") is False


def test_powermetrics_throttle_pressure_level():
    assert sampler.parse_powermetrics_throttle("Current pressure level: Nominal") is False
    assert sampler.parse_powermetrics_throttle("Current pressure level: Heavy") is True


def test_powermetrics_throttle_speed_limit():
    assert sampler.parse_powermetrics_throttle("CPU speed limit: 100.00%") is False
    assert sampler.parse_powermetrics_throttle("CPU speed limit: 72.00%") is True
