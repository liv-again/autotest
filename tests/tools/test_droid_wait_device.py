# tests/tools/test_droid_wait_device.py
import tools.droid as droid


def _fake_adb(out):
    def _adb(*args, **kwargs):
        return (0, out, "")
    return _adb


def test_wait_device_found_immediately(monkeypatch):
    calls = []
    def _adb(*args, **kwargs):
        calls.append(args)
        return (0, "List of devices attached\nc923178d\tdevice\n\n", "")
    monkeypatch.setattr(droid, "adb", _adb)
    assert droid.wait_device(tries=3, interval=0) == 0
    assert len(calls) == 1  # 第一次就命中，不再重试


def test_wait_device_no_device_returns_1(monkeypatch):
    calls = []
    def _adb(*args, **kwargs):
        calls.append(args)
        return (0, "List of devices attached\n\n", "")
    monkeypatch.setattr(droid, "adb", _adb)
    assert droid.wait_device(tries=3, interval=0) == 1
    assert len(calls) == 3  # 三次探测均失败


def test_wait_device_offline_not_counted(monkeypatch):
    monkeypatch.setattr(droid, "adb", _fake_adb("List of devices attached\nemulator-5554\toffline\n\n"))
    assert droid.wait_device(tries=3, interval=0) == 1


def test_wait_device_recovers_on_retry(monkeypatch):
    state = {"n": 0}
    def _adb(*args, **kwargs):
        state["n"] += 1
        if state["n"] >= 3:
            return (0, "List of devices attached\nc923178d\tdevice\n\n", "")
        return (0, "List of devices attached\n\n", "")
    monkeypatch.setattr(droid, "adb", _adb)
    assert droid.wait_device(tries=3, interval=0) == 0
    assert state["n"] == 3  # 第三次探测恢复在线
