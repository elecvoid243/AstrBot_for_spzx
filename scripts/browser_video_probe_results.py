"""Reduce raw engineering probe evidence without hiding superseded input samples."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path

from PIL import Image, ImageChops, ImageStat


def distribution(values):
    """Describe measured samples using nearest-rank percentiles.

    Args:
        values: Numeric observations in their documented units.

    Returns:
        Sample count, mean, median, p95 and maximum, or an empty count.
    """
    values = sorted(values)
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": statistics.mean(values),
        "p50": statistics.median(values),
        "p95": values[math.ceil(len(values) * 0.95) - 1],
        "max": values[-1],
    }


def main():
    """Persist traceable descriptive results for completed fixture cases."""
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    results = []
    for folder in sorted(args.root.iterdir()):
        if (
            not (folder / "viewer.json").exists()
            or not (folder / "runtime.json").exists()
        ):
            continue
        viewer = json.loads((folder / "viewer.json").read_text(encoding="utf-8"))
        runtime = json.loads((folder / "runtime.json").read_text(encoding="utf-8"))
        frames = viewer["frames"]
        for frame in frames:
            if "expectedDisplayTime" in frame and not frame.get(
                "presentation_adjusted"
            ):
                frame["callback_t"] = frame["t"]
                # Preserve an upper bound: callback processing time is already
                # included, then add any compositor display lead still remaining.
                frame["t"] += max(
                    0, frame["expectedDisplayTime"] - frame["callbackTime"]
                )
                frame["presentation_adjusted"] = True
        inputs = {item["id"]: item for item in viewer["inputs"]}
        target_events = {
            item["id"]: item for item in viewer["target"]["target"]["probe"]["events"]
        }
        seen = {}
        valid_frames = []
        for frame in frames:
            # The optical words are decoded from pixels, not from SSE or ACKs.
            if not 0 <= frame["words"][0] <= frame["t"]:
                continue
            valid_frames.append(frame)
            if frame["words"][1] in inputs and frame["words"][1] in target_events:
                seen.setdefault(frame["words"][1], frame)
        stamps = {item["rtp_timestamp"] for item in runtime["send"]}
        phases = []
        for phase in viewer["phases"]:
            selected = [
                frame
                for frame in valid_frames
                if phase["start"] <= frame["t"] <= phase["end"]
            ]
            phases.append(
                {
                    **phase,
                    "frames": len(selected),
                    "fps": len(selected) * 1000 / (phase["end"] - phase["start"]),
                    "source_age_ms": distribution(
                        [frame["t"] - frame["words"][0] for frame in selected]
                    ),
                    "input_to_present_ms": distribution(
                        [
                            frame["t"] - inputs[number]["t"]
                            for number, frame in seen.items()
                            if phase["start"] <= inputs[number]["t"] <= phase["end"]
                        ]
                    ),
                }
            )
        # CPU uses Windows psutil convention: 100% is one logical core. The
        # fixture samples its own runtime and private browser descendants only.
        resources = runtime["resources"][2:]
        span = (frames[-1]["t"] - frames[0]["t"]) / 1000
        inbound = [
            item
            for item in viewer["stats"]
            if item.get("type") == "inbound-rtp" and item.get("kind") == "video"
        ]
        result = {
            "folder": str(folder.resolve()),
            "mode": runtime["mode"],
            "viewport": runtime["size"],
            "window_s": span,
            "presented": len(frames),
            "presented_fps": (len(frames) - 1) / span,
            "valid_optical_frames": len(valid_frames),
            "repeated_source_frames": len(valid_frames)
            - len({frame["words"][3] for frame in valid_frames}),
            "present_gap_ms": distribution(
                [b["t"] - a["t"] for a, b in zip(frames, frames[1:])]
            ),
            "source_age_ms": distribution(
                [frame["t"] - frame["words"][0] for frame in valid_frames]
            ),
            "input_to_present_ms": distribution(
                [frame["t"] - inputs[number]["t"] for number, frame in seen.items()]
            ),
            "input_to_target_ms": distribution(
                [target_events[number]["t"] - inputs[number]["t"] for number in seen]
            ),
            "target_to_present_ms": distribution(
                [
                    frame["t"] - target_events[number]["t"]
                    for number, frame in seen.items()
                ]
            ),
            "inputs": len(inputs),
            "target_events": len(target_events),
            "optically_observed_input_ids": len(seen),
            "applied_but_not_individually_observed": len(
                set(target_events) - set(seen)
            ),
            "ack_status": dict(Counter(item.get("status") for item in viewer["acks"])),
            "capture_events": len(runtime["capture"]),
            "capture_bytes": sum(item["bytes"] for item in runtime["capture"]),
            "capture_screenshot_ms": distribution(
                [item["ms"] for item in runtime["capture"] if "ms" in item]
            ),
            "decode_ms": distribution([item["ms"] for item in runtime["decode"]]),
            "encode_ms": distribution([item["ms"] for item in runtime["encode"]]),
            "frame_queue_ms": distribution(
                [item["queue_ms"] for item in runtime["decode"]]
            ),
            "send_first_packet_ms": distribution(
                [item["send_ms"] for item in runtime["send"]]
            ),
            "encoded_bytes": sum(item["bytes"] for item in runtime["encode"]),
            "encoded_mbps": sum(item["bytes"] for item in runtime["encode"])
            * 8
            / span
            / 1e6,
            "queue_max": runtime["queue_max"],
            "capture_drops": runtime["dropped"],
            "matched_rtp_frames": sum(frame.get("rtp") in stamps for frame in frames),
            "runtime_and_private_browser_cpu_percent": distribution(
                [sum(row["cpu"] for row in sample["processes"]) for sample in resources]
            ),
            "runtime_and_private_browser_rss_bytes": distribution(
                [sum(row["rss"] for row in sample["processes"]) for sample in resources]
            ),
            "runtime_cpu_percent": distribution(
                [sample["processes"][0]["cpu"] for sample in resources]
            ),
            "browser_inbound_stats": inbound,
            "phases": phases,
            "target": {
                key: value
                for key, value in viewer["target"]["target"].items()
                if key != "probe"
            },
            "limitations": [
                "Local headless receiver; not production ChatUI",
                "Optical timestamp and input markers add a small animated area",
                "Frame binding is for a fixed document only",
                "Inputs without individually observed markers are reported separately",
                "RSS sums include shared pages",
                "JPEG presentation uses onload plus rAF; video uses requestVideoFrameCallback",
            ],
        }
        if viewer.get("owned_resources"):
            result["all_owned_process_cpu_percent"] = distribution(
                [
                    sum(row["cpu"] for row in sample["processes"])
                    for sample in viewer["owned_resources"][2:]
                ]
            )
            result["all_owned_process_rss_bytes"] = distribution(
                [
                    sum(row["rss"] for row in sample["processes"])
                    for sample in viewer["owned_resources"][2:]
                ]
            )
        if (folder / "display.png").exists() and (folder / "reference.png").exists():
            with (
                Image.open(folder / "display.png") as display,
                Image.open(folder / "reference.png") as reference,
            ):
                source_size = list(display.size)
                display = display.convert("RGB").resize(
                    reference.size, Image.Resampling.LANCZOS
                )
                reference = reference.convert("RGB")
                crop = (24, 112, 560, 134)
                delta = ImageChops.difference(display.crop(crop), reference.crop(crop))
                mse = statistics.mean(ImageStat.Stat(delta).sum2) / (
                    delta.width * delta.height
                )
                result["small_text_quality"] = {
                    "source_size": source_size,
                    "reference_size": list(reference.size),
                    "crop": list(crop),
                    "mse_rgb": mse,
                    "psnr_db": 10 * math.log10(255**2 / mse) if mse else None,
                    "comparison": "Decoded displayed pixels versus lossless target screenshot; static small-text crop; JPEG higher-resolution raster is resized to the viewport",
                }
                display.crop(crop).save(folder / "text-displayed.png")
                reference.crop(crop).save(folder / "text-reference.png")
        results.append(result)
        (folder / "metrics.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print(
            folder.name,
            "fps",
            round(result["presented_fps"], 2),
            "e2e",
            result["input_to_present_ms"],
            "encode",
            result["encode_ms"],
            flush=True,
        )
    (args.root / "results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
