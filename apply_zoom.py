import argparse
import json
import os
import shlex
import subprocess


def smoothstep(unit):
    unit = min(max(unit, 0.0), 1.0)
    return unit * unit * (3.0 - 2.0 * unit)


def clamp(value, low, high):
    return min(max(value, low), high)


def crop_origin(px, py, zoom, width, height):
    if zoom <= 0:
        raise ValueError("zoom must be positive")
    max_x = width - width / zoom
    max_y = height - height / zoom
    origin_x = clamp(px - width / (2.0 * zoom), 0.0, max_x)
    origin_y = clamp(py - height / (2.0 * zoom), 0.0, max_y)
    return origin_x, origin_y


def ramp_zoom_at_frame(frame_index, ramp_frames, zoom_factor, direction):
    if ramp_frames <= 0:
        unit = 1.0
    else:
        unit = frame_index / float(ramp_frames)
    eased = smoothstep(unit)
    if direction == "in":
        return 1.0 + (zoom_factor - 1.0) * eased
    return zoom_factor - (zoom_factor - 1.0) * eased


def assign_types(events):
    ordered = sorted(events, key=lambda item: float(item["t"]))
    if ordered and all(item.get("type") in ("in", "out") for item in ordered):
        return [
            {
                "t": float(item["t"]),
                "x": float(item["x"]),
                "y": float(item["y"]),
                "type": item["type"],
            }
            for item in ordered
        ]

    typed = []
    expect = "in"
    for item in ordered:
        kind = item.get("type")
        if kind not in ("in", "out"):
            kind = expect
        typed.append(
            {
                "t": float(item["t"]),
                "x": float(item["x"]),
                "y": float(item["y"]),
                "type": kind,
            }
        )
        expect = "out" if kind == "in" else "in"
    return typed


def pair_events(events, duration=None):
    typed = assign_types(events)
    pairs = []
    index = 0
    while index < len(typed):
        event = typed[index]
        if event["type"] != "in":
            index += 1
            continue
        following = typed[index + 1] if index + 1 < len(typed) else None
        if following and following["type"] == "out":
            pairs.append(
                {
                    "t_in": event["t"],
                    "x": event["x"],
                    "y": event["y"],
                    "t_out": following["t"],
                }
            )
            index += 2
        else:
            pairs.append(
                {
                    "t_in": event["t"],
                    "x": event["x"],
                    "y": event["y"],
                    "t_out": None,
                    "duration": duration,
                }
            )
            index += 1
    return pairs


def zoom_at(t, pairs, zoom_factor, ramp_sec, duration):
    level = 1.0
    for pair in pairs:
        t_in = pair["t_in"]
        t_out = pair["t_out"]
        if t < t_in:
            continue
        if ramp_sec > 0 and t < t_in + ramp_sec:
            unit = (t - t_in) / ramp_sec
            level = 1.0 + (zoom_factor - 1.0) * smoothstep(unit)
            continue
        if t_out is None:
            if duration is None or t <= duration:
                level = zoom_factor
            continue
        if t < t_out:
            level = zoom_factor
            continue
        if ramp_sec > 0 and t < t_out + ramp_sec:
            unit = (t - t_out) / ramp_sec
            level = zoom_factor - (zoom_factor - 1.0) * smoothstep(unit)
            continue
        level = 1.0
    return level


def focus_at(t, pairs, ramp_sec):
    point = None
    for pair in pairs:
        t_in = pair["t_in"]
        t_out = pair["t_out"]
        if t < t_in:
            continue
        if t_out is None:
            point = (pair["x"], pair["y"])
            continue
        if t < t_out + ramp_sec:
            point = (pair["x"], pair["y"])
            continue
    return point


def parse_sidecar(data):
    viewport = data.get("viewport") or {}
    events = data.get("events") or []
    return {
        "width": int(viewport.get("width", 1280)),
        "height": int(viewport.get("height", 720)),
        "fps": float(data.get("fps", 25)),
        "zoom_factor": float(data.get("zoom_factor", 2.0)),
        "ramp_sec": float(data.get("ramp_sec", 0.5)),
        "events": assign_types(events) if events else [],
    }


def sidecar_path_for(video_path):
    return video_path + ".zoom.json"


def output_path_for(video_path):
    root, ext = os.path.splitext(video_path)
    return root + ".zoomed" + (ext or ".webm")


def load_sidecar(video_path):
    path = sidecar_path_for(video_path)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return parse_sidecar(json.load(handle))


def _num(value):
    return f"{float(value):.6f}"


def _ease_expr(unit_expr):
    return f"(({unit_expr})*({unit_expr})*(3-2*({unit_expr})))"


def _unit_expr(clock, start, ramp):
    return f"clip(({clock}-({_num(start)}))/({_num(ramp)}),0,1)"


def _zoom_piece(clock, pair, zoom_factor, ramp_sec):
    ramp = ramp_sec if ramp_sec > 0 else 0.000001
    zoom_in = f"(1+({_num(zoom_factor)}-1)*{_ease_expr(_unit_expr(clock, pair['t_in'], ramp))})"
    if pair["t_out"] is None:
        window = f"gte({clock},{_num(pair['t_in'])})"
        inside = f"if(lt({clock},{_num(pair['t_in'] + ramp)}),{zoom_in},{_num(zoom_factor)})"
        return window, inside
    zoom_out = (
        f"({_num(zoom_factor)}-({_num(zoom_factor)}-1)*"
        f"{_ease_expr(_unit_expr(clock, pair['t_out'], ramp))})"
    )
    window = (
        f"gte({clock},{_num(pair['t_in'])})*lt({clock},{_num(pair['t_out'] + ramp)})"
    )
    inside = (
        f"if(lt({clock},{_num(pair['t_in'] + ramp)}),{zoom_in},"
        f"if(lt({clock},{_num(pair['t_out'])}),{_num(zoom_factor)},{zoom_out}))"
    )
    return window, inside


def build_zoompan_filter(sidecar, pairs):
    fps = sidecar["fps"]
    clock = f"(in/{_num(fps)})"
    zoom_expr = "1"
    px_expr = f"in_w/2"
    py_expr = f"in_h/2"
    for pair in reversed(pairs):
        window, inside = _zoom_piece(clock, pair, sidecar["zoom_factor"], sidecar["ramp_sec"])
        zoom_expr = f"if({window},{inside},{zoom_expr})"
        px_expr = f"if({window},{_num(pair['x'])},{px_expr})"
        py_expr = f"if({window},{_num(pair['y'])},{py_expr})"

    origin_x = f"clip({px_expr}-in_w/(2*zoom),0,in_w-in_w/zoom)"
    origin_y = f"clip({py_expr}-in_h/(2*zoom),0,in_h-in_h/zoom)"
    escaped_zoom = zoom_expr.replace(",", r"\,")
    escaped_x = origin_x.replace(",", r"\,")
    escaped_y = origin_y.replace(",", r"\,")
    width = int(sidecar["width"])
    height = int(sidecar["height"])
    fps_text = _num(fps)
    return (
        f"fps={fps_text},"
        f"zoompan=z='{escaped_zoom}':x='{escaped_x}':y='{escaped_y}':"
        f"d=1:s={width}x{height}:fps={fps_text}"
    )


def build_ffmpeg_command(video_path, ffmpeg_bin="ffmpeg", output_path=None, sidecar=None):
    output_path = output_path or output_path_for(video_path)
    if sidecar is None:
        sidecar = load_sidecar(video_path)
    pairs = pair_events(sidecar["events"]) if sidecar else []
    if not pairs:
        command = [ffmpeg_bin, "-y", "-i", video_path, "-c", "copy", output_path]
        return command, output_path
    video_filter = build_zoompan_filter(sidecar, pairs)
    command = [
        ffmpeg_bin,
        "-y",
        "-i",
        video_path,
        "-vf",
        video_filter,
        "-c:v",
        "libvpx-vp9",
        "-b:v",
        "0",
        "-crf",
        "33",
        "-an",
        output_path,
    ]
    return command, output_path


def process_video(video_path, ffmpeg_bin="ffmpeg", output_path=None):
    command, output_path = build_ffmpeg_command(
        video_path, ffmpeg_bin=ffmpeg_bin, output_path=output_path
    )
    subprocess.run(command, check=True)
    return output_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render a Camtasia-style zoom onto a recorded video")
    parser.add_argument("--video", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args(argv)

    command, _output = build_ffmpeg_command(args.video, ffmpeg_bin=args.ffmpeg)
    if args.dry_run:
        print(shlex.join(command))
        return 0
    subprocess.run(command, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
