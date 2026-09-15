import tools.droid as droid


def test_tap_bbox_uses_center(monkeypatch):
    calls = []

    def _adb(*args, **kwargs):
        calls.append(args)
        return (0, "", "")

    monkeypatch.setattr(droid, "adb", _adb)

    assert droid.tap_bbox("100", "200", "300", "600") == 0
    assert calls == [("shell", "input", "tap", "200", "400")]


def test_tap_bbox_rejects_invalid_box(monkeypatch):
    calls = []
    monkeypatch.setattr(droid, "adb", lambda *args, **kwargs: calls.append(args))

    assert droid.tap_bbox(300, 600, 100, 200) == 2
    assert calls == []


def test_tap_bbox_reports_adb_failure(monkeypatch):
    monkeypatch.setattr(
        droid,
        "adb",
        lambda *args, **kwargs: (1, "", "input tap failed"),
    )

    assert droid.tap_bbox(100, 200, 300, 600) == 1


def test_dump_xml_reports_pull_failure(monkeypatch, tmp_path):
    def _adb(*args, **kwargs):
        if args[0] == "shell":
            return (0, "", "")
        return (1, "", "device disconnected")

    monkeypatch.setattr(droid, "adb", _adb)

    try:
        droid.dump_xml(str(tmp_path / "ui.xml"))
    except RuntimeError as exc:
        assert "adb pull UI 树失败" in str(exc)
        assert "device disconnected" in str(exc)
    else:
        raise AssertionError("dump_xml should report adb pull failure")


def test_text_and_id_taps_ignore_hidden_or_disabled_nodes(monkeypatch):
    xml = """
    <hierarchy>
      <node text="入口" bounds="[0,0][10,10]" clickable="true" visible-to-user="false" />
      <node text="入口" resource-id="pkg:id/entry" bounds="[20,20][40,40]"
            clickable="true" enabled="true" visible-to-user="true" />
      <node resource-id="pkg:id/disabled" bounds="[50,50][70,70]"
            clickable="true" enabled="false" visible-to-user="true" />
    </hierarchy>
    """
    calls = []
    monkeypatch.setattr(droid, "dump_xml", lambda *args, **kwargs: xml)
    monkeypatch.setattr(droid, "adb", lambda *args, **kwargs: calls.append(args) or (0, "", ""))

    assert droid.tap_text("入口") == 0
    assert droid.tap_id("disabled") == 1
    assert calls == [("shell", "input", "tap", "30", "30")]
