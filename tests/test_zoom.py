import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apply_zoom
import web_service


def _pairs(events, duration=None):
    return apply_zoom.pair_events(events, duration=duration)


def test_ramp_frames_use_smoothstep():
    assert apply_zoom.ramp_zoom_at_frame(0, 10, 2.0, "in") == pytest.approx(1.0)
    assert apply_zoom.ramp_zoom_at_frame(10, 10, 2.0, "in") == pytest.approx(2.0)
    assert apply_zoom.ramp_zoom_at_frame(0, 10, 2.0, "out") == pytest.approx(2.0)
    assert apply_zoom.ramp_zoom_at_frame(10, 10, 2.0, "out") == pytest.approx(1.0)
    mid_ramp = apply_zoom.ramp_zoom_at_frame(2.5, 10, 2.0, "in")
    assert mid_ramp == pytest.approx(1.0 + 0.15625)
    assert mid_ramp != pytest.approx(1.25)


def test_crop_stays_inside_the_frame_and_centers_on_the_point():
    width, height, zoom = 1280, 720, 2.0
    origin_x, origin_y = apply_zoom.crop_origin(640, 360, zoom, width, height)
    assert origin_x == pytest.approx(320)
    assert origin_y == pytest.approx(180)
    assert origin_x + width / (2 * zoom) == pytest.approx(640)
    assert origin_y + height / (2 * zoom) == pytest.approx(360)

    left, top = apply_zoom.crop_origin(0, 0, zoom, width, height)
    right, bottom = apply_zoom.crop_origin(width, height, zoom, width, height)
    assert (left, top) == (0, 0)
    assert right == pytest.approx(width - width / zoom)
    assert bottom == pytest.approx(height - height / zoom)
    assert 0 <= right <= width - width / zoom
    assert 0 <= bottom <= height - height / zoom


def test_multiple_pairs_and_open_ended_hold():
    events = [
        {"t": 1.0, "x": 100, "y": 80, "type": "in"},
        {"t": 3.0, "x": 100, "y": 80, "type": "out"},
        {"t": 5.0, "x": 400, "y": 200, "type": "in"},
        {"t": 7.0, "x": 400, "y": 200, "type": "out"},
    ]
    pairs = _pairs(events, duration=12)
    assert len(pairs) == 2
    assert apply_zoom.zoom_at(2.0, pairs, 2.0, 0.5, 12) == pytest.approx(2.0)
    assert apply_zoom.zoom_at(4.0, pairs, 2.0, 0.5, 12) == pytest.approx(1.0)
    assert apply_zoom.zoom_at(6.0, pairs, 2.0, 0.5, 12) == pytest.approx(2.0)
    assert apply_zoom.focus_at(6.0, pairs, 0.5) == (400, 200)

    open_pairs = _pairs([{"t": 2.0, "x": 10, "y": 20, "type": "in"}], duration=10)
    assert open_pairs[0]["t_out"] is None
    assert apply_zoom.zoom_at(1.0, open_pairs, 2.0, 0.5, 10) == pytest.approx(1.0)
    assert apply_zoom.zoom_at(9.0, open_pairs, 2.0, 0.5, 10) == pytest.approx(2.0)
    assert apply_zoom.focus_at(9.0, open_pairs, 0.5) == (10, 20)


def test_sidecar_parsing_old_events_and_copy_path(tmp_path):
    example = {
        "viewport": {"width": 1280, "height": 720},
        "fps": 25,
        "zoom_factor": 2.0,
        "ramp_sec": 0.5,
        "events": [
            {"t": 3.0, "x": 640, "y": 360, "type": "in"},
            {"t": 8.0, "x": 640, "y": 360, "type": "out"},
        ],
    }
    parsed = apply_zoom.parse_sidecar(example)
    assert parsed["events"][0]["type"] == "in"
    assert parsed["fps"] == 25

    old = apply_zoom.parse_sidecar(
        {"events": [{"t": 1, "x": 5, "y": 6}, {"t": 4, "x": 7, "y": 8}]}
    )
    assert [item["type"] for item in old["events"]] == ["in", "out"]
    assert old["width"] == 1280

    video = tmp_path / "plain.webm"
    video.write_bytes(b"video")
    command, output = apply_zoom.build_ffmpeg_command(str(video), ffmpeg_bin="ffmpeg")
    assert "zoompan" not in command
    assert "copy" in command
    assert output.endswith("plain.zoomed.webm")

    empty = tmp_path / "empty.webm.zoom.json"
    source = tmp_path / "empty.webm"
    source.write_bytes(b"video")
    empty.write_text(json.dumps({"events": []}), encoding="utf-8")
    command, _output = apply_zoom.build_ffmpeg_command(str(source))
    assert "copy" in command
    assert "zoompan" not in command


def test_dry_run_prints_ffmpeg_without_encoding(tmp_path, capsys):
    video = tmp_path / "ACME_GmbH.webm"
    video.write_bytes(b"video")
    sidecar = tmp_path / "ACME_GmbH.webm.zoom.json"
    sidecar.write_text(
        json.dumps(
            {
                "viewport": {"width": 1280, "height": 720},
                "fps": 25,
                "zoom_factor": 2.0,
                "ramp_sec": 0.5,
                "events": [
                    {"t": 3.0, "x": 640, "y": 360, "type": "in"},
                    {"t": 8.0, "x": 640, "y": 360, "type": "out"},
                ],
            }
        ),
        encoding="utf-8",
    )
    code = apply_zoom.main(["--video", str(video), "--dry-run", "--ffmpeg", "/usr/bin/ffmpeg"])
    assert code == 0
    printed = capsys.readouterr().out
    assert "zoompan=" in printed
    assert "/usr/bin/ffmpeg" in printed
    assert not (tmp_path / "ACME_GmbH.zoomed.webm").exists()
    command, _output = apply_zoom.build_ffmpeg_command(str(video), ffmpeg_bin="/usr/bin/ffmpeg")
    assert command[0] == "/usr/bin/ffmpeg"
    assert "libvpx-vp9" in command
    assert any("zoompan=" in part for part in command)


def test_dockerfile_and_process_route():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dockerfile = open(os.path.join(root, "Dockerfile"), encoding="utf-8").read()
    assert "ffmpeg" in dockerfile
    rules = {rule.rule for rule in web_service.app.url_map.iter_rules()}
    assert "/jobs/<int:job_id>/process" in rules


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_ffmpeg_renders_a_zoomed_webm(tmp_path):
    source = tmp_path / "clip.webm"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=25:duration=2",
            "-c:v",
            "libvpx-vp9",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    sidecar = {
        "viewport": {"width": 320, "height": 180},
        "fps": 25,
        "zoom_factor": 2.0,
        "ramp_sec": 0.4,
        "events": [{"t": 0.2, "x": 80, "y": 40, "type": "in"}],
    }
    (tmp_path / "clip.webm.zoom.json").write_text(json.dumps(sidecar), encoding="utf-8")
    output = apply_zoom.process_video(str(source))
    assert os.path.exists(output)
    assert os.path.getsize(output) > 0
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            output,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert float(probe.stdout.strip()) > 1.0
