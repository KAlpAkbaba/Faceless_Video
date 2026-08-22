"""Command line entry point.

    python -m pipeline.cli doctor          # check config and credentials
    python -m pipeline.cli plan            # show the cost estimate, spend nothing
    python -m pipeline.cli probe           # verify the LTX API shape with one cheap job
    python -m pipeline.cli auth            # mint YouTube credentials (run locally, once)
    python -m pipeline.cli run             # the full daily pipeline
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

from .budget import BudgetExceeded, estimate_run_cost
from .config import REPO_ROOT, Config, ConfigError
from .run import CHARS_PER_SECOND, RunOptions, run


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
        datefmt="%H:%M:%S",
    )
    # These two are chatty at INFO and drown out the pipeline's own log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pipeline", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-c", "--config", default=None, help="Path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Check configuration, credentials and tooling.")
    sub.add_parser("plan", help="Print the cost estimate for a run without spending anything.")

    probe = sub.add_parser("probe", help="Submit one clip and dump the raw API responses.")
    probe.add_argument("--prompt", default="A slow pan across an empty workshop bench at dawn, dust in the light.")
    probe.add_argument(
        "--resolution",
        default=None,
        help="Defaults to longform.resolution. Use --discover if the API rejects it.",
    )
    probe.add_argument("--seconds", type=float, default=None, help="Defaults to video.clip_seconds.")
    probe.add_argument("--model", default=None, help="Override video.model for this probe.")
    probe.add_argument(
        "--image",
        action="store_true",
        help="Probe image-to-video instead, using the first reference frame.",
    )
    probe.add_argument(
        "--discover",
        action="store_true",
        help="Try each known resolution spelling until the API accepts one. "
             "Rejected attempts are not billed.",
    )

    auth = sub.add_parser("auth", help="Mint YouTube OAuth credentials. Run this locally, once.")
    auth.add_argument(
        "--client-secret",
        default=None,
        help="Path to the OAuth client secret JSON. Found automatically when omitted.",
    )

    story = sub.add_parser(
        "storyboard",
        help="Write one episode and its shot list, with a costing. No video is generated.",
    )
    story.add_argument("--output-dir", default=None)

    run_cmd = sub.add_parser("run", help="Run the full pipeline.")
    run_cmd.add_argument("--only", choices=["both", "longform", "shorts"], default="both")
    run_cmd.add_argument("--no-upload", action="store_true", help="Render locally, do not publish.")
    run_cmd.add_argument(
        "--max-shots",
        type=int,
        default=None,
        help="Generate at most this many shots per cut. Use it to test character "
             "consistency on a minute of footage before paying for five.",
    )
    run_cmd.add_argument("--work-dir", default=None)
    run_cmd.add_argument("--output-dir", default=None)
    return parser


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_doctor(config: Config) -> int:
    print(f"config           {config.path}")
    print(f"channel          {config.get('channel.name')}")
    print(f"video provider   {config.get('video.provider')} / {config.get('video.model')}")
    print(f"voice provider   {config.get('voice.provider')}")
    print(f"publish          {config.get('publish.publish_at_local')} {config.get('publish.timezone')}"
          f"  (shorts {config.get('publish.shorts_publish_at_local')})")

    ok = True
    for tool in ("ffmpeg", "ffprobe"):
        found = shutil.which(tool)
        print(f"{tool:<16} {found or 'MISSING'}")
        ok = ok and bool(found)

    secrets = config.secrets
    needed = [("ANTHROPIC_API_KEY", secrets.anthropic_api_key, True)]
    if config.get("video.provider") == "ltx":
        needed.append(("LTX_API_KEY", secrets.ltx_api_key, True))
    else:
        needed.append(("FAL_KEY", secrets.fal_api_key, True))
    if config.get("voice.provider") == "elevenlabs":
        needed.append(("ELEVENLABS_API_KEY", secrets.elevenlabs_api_key, True))
    needed += [
        ("YOUTUBE_CLIENT_ID", secrets.youtube_client_id, False),
        ("YOUTUBE_CLIENT_SECRET", secrets.youtube_client_secret, False),
        ("YOUTUBE_REFRESH_TOKEN", secrets.youtube_refresh_token, False),
    ]
    print()
    for name, value, required in needed:
        status = "set" if value else ("MISSING" if required else "missing (upload will fail)")
        print(f"{name:<24} {status}")
        ok = ok and (bool(value) or not required)

    print()
    if ok:
        print("OK")
        return 0

    print("Problems found — see MISSING entries above.")
    print(
        "\nLocally, put the missing values in a .env file at the repository root "
        "(copy .env.example)\nand they are picked up automatically. In GitHub Actions "
        "they come from repository secrets.\nReal environment variables always win over "
        ".env, so the two never fight."
    )
    return 1


def cmd_plan(config: Config) -> int:
    do_longform = bool(config.get("longform.enabled", True))
    do_shorts = bool(config.get("shorts.enabled", True))
    regenerate = str(config.get("shorts.clip_strategy", "crop")) == "regenerate"
    model = str(config.require("video.model"))
    prices = config.get("budget.ltx_price_per_second", {}) or {}

    narration_seconds = (float(config.get("longform.target_seconds", 360)) if do_longform else 0) + (
        float(config.get("shorts.target_seconds", 50)) if do_shorts else 0
    )
    estimate = estimate_run_cost(
        longform_clips=int(config.get("video.max_clips", 12)) if do_longform else 0,
        shorts_clips=int(config.get("shorts.max_clips", 6)) if (do_shorts and regenerate) else 0,
        clip_seconds=float(config.get("video.clip_seconds", 8)),
        price_per_second=float(prices.get(model, 0.04)),
        narration_chars=int(narration_seconds * CHARS_PER_SECOND),
        voice_provider=str(config.get("voice.provider", "edge")),
    )
    ceiling = float(config.get("budget.max_usd_per_run", 5.0))
    print(estimate.render())
    print(f"\n  ceiling  ${ceiling:.2f} per run   ->  ~${estimate.total * 30:.0f}/month at one run per day")
    if estimate.total > ceiling:
        print("\nOver the ceiling — `run` would abort. Lower max_clips or raise budget.max_usd_per_run.")
        return 1
    return 0


def cmd_probe(config: Config, args: argparse.Namespace) -> int:
    from .providers import build_provider

    if args.model:
        config.data.setdefault("video", {})["model"] = args.model

    # The model decides which resolutions are legal, so probe with the one the
    # real run will use rather than a cheaper value it may reject outright.
    resolution = args.resolution or str(config.get("longform.resolution", "1080p"))
    seconds = args.seconds if args.seconds is not None else float(config.get("video.clip_seconds", 8))

    provider = build_provider(config)
    mode = "discovering a working resolution" if args.discover else f"at {resolution}, {seconds:g}s"
    print(f"Probing {config.get('video.provider')} / {config.get('video.model')} {mode}\n")
    try:
        if args.image:
            from .references import load_library

            frame = load_library(config).frames[0]
            print(f"Using reference frame: {frame.name}\n")
            discover = getattr(provider, "discover_image_to_video", None)
            if discover is None:
                print(f"{provider.name} has no image discovery mode.", file=sys.stderr)
                return 2
            report = discover(args.prompt, seconds=seconds, reference=frame)
        elif args.discover:
            discover = getattr(provider, "discover", None)
            if discover is None:
                print(f"{provider.name} has no discovery mode.", file=sys.stderr)
                return 2
            report = discover(args.prompt, seconds=seconds)
        else:
            report = provider.probe(args.prompt, resolution=resolution, seconds=seconds)
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()
    print(json.dumps(report, indent=2, default=str))
    print(
        "\nIf the paths above are wrong for your account, pin them with the "
        "LTX_API_BASE / LTX_SUBMIT_PATH / LTX_STATUS_PATH environment variables."
    )
    return 0


def cmd_auth(client_secret: str | None) -> int:
    from .youtube import find_client_secret, run_auth_flow

    path = find_client_secret(Path(client_secret) if client_secret else None)
    credentials = run_auth_flow(path)
    print("\nAdd these three as GitHub repository secrets:\n")
    for key, value in credentials.items():
        print(f"  {key}={value}")
    print(
        "\nIf the OAuth consent screen is still in 'Testing', publish it first — "
        "test-mode refresh tokens stop working after 7 days."
    )
    return 0


def cmd_storyboard(config: Config, args: argparse.Namespace) -> int:
    """Plan one episode end to end without generating a frame.

    The only spend is the two Claude calls. Everything the render would cost is
    reported instead — which is the cheapest way to find out whether an episode
    is affordable before committing to it.
    """
    from datetime import datetime, timezone

    from .budget import shots_for_duration
    from .state import History
    from .writer import Writer, write_debug_bundle

    history = History(config.history_path, keep_last=int(config.get("state.dedupe_last_n", 120)))
    writer = Writer(config)
    idea = writer.pick_topic(history.recent_titles(), history.recent_slugs())
    package = writer.write_scripts(idea)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    out = Path(args.output_dir) if args.output_dir else (REPO_ROOT / "out" / f"storyboard-{stamp}-{idea.slug}")
    write_debug_bundle(out, idea, package)

    clip_seconds = float(config.get("video.clip_seconds", 8))
    reuse = bool(config.get("video.reuse_clips", True))
    prices = config.get("budget.ltx_price_per_second", {}) or {}
    model = str(config.require("video.model"))

    for cut, script in (("EPISODE", package.longform), ("SHORT", package.shorts)):
        words = len(script.narration.split())
        print(f"\n{'=' * 72}\n{cut}: {script.title}\n{'=' * 72}")
        print(f"{words} words of narration -> about {words / 2.5:.0f}s of runtime")
        print(f"{len(script.shots)} shots\n")
        for index, shot in enumerate(script.shots, 1):
            print(f"{index:>3}. [{shot.beat_label}]\n     {shot.prompt}")

    print(f"\n{'=' * 72}\nCOSTING\n{'=' * 72}")
    print(f"clip length      {clip_seconds:g}s")
    print(f"reuse_clips      {reuse}"
          f"{'  (shots looped across the timeline)' if reuse else '  (every shot plays once)'}")

    total_shots = len(package.longform.shots) + len(package.shorts.shots)
    generated = total_shots * clip_seconds
    print(f"shots            {len(package.longform.shots)} + {len(package.shorts.shots)}"
          f" = {total_shots}")
    print(f"generated video  {generated:.0f}s\n")

    known = dict(prices)
    known.setdefault(model, 0.13)
    print(f"  {'model':<16} {'$/s':>6} {'per episode':>12} {'x6/week':>10} {'per month':>11}")
    print("  " + "-" * 60)
    for name, price in sorted(known.items(), key=lambda kv: -kv[1]):
        episode = generated * price + 0.15
        marker = "  <- configured" if name == model else ""
        print(f"  {name:<16} {price:>6.2f} {episode:>11.2f}$ {episode * 6:>9.2f}$ "
              f"{episode * 6 * 4.33:>10.0f}${marker}")

    print(
        "\nThese rates are unverified. Render this one episode, then read the real"
        "\ncharge off the LTX billing page and correct budget.ltx_price_per_second."
    )
    print(f"\nWritten to {out}")
    return 0


def cmd_run(config: Config, args: argparse.Namespace) -> int:
    options = RunOptions(
        only=args.only,
        upload=not args.no_upload,
        work_dir=Path(args.work_dir) if args.work_dir else None,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        max_shots=args.max_shots,
    )
    report = run(config, options)
    print("\n" + report.summary())
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)

    if args.command == "auth":
        # Authorisation runs before any config or credentials exist, so it sits
        # outside the config load below — but it still needs the same clean
        # error reporting rather than a traceback.
        try:
            return cmd_auth(args.client_secret)
        except ConfigError as exc:
            print(f"\n{exc}", file=sys.stderr)
            return 2

    try:
        config = Config.load(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "doctor":
            return cmd_doctor(config)
        if args.command == "plan":
            return cmd_plan(config)
        if args.command == "probe":
            return cmd_probe(config, args)
        if args.command == "storyboard":
            return cmd_storyboard(config, args)
        if args.command == "run":
            return cmd_run(config, args)
    except BudgetExceeded as exc:
        print(f"\nBudget stop: {exc}", file=sys.stderr)
        return 3
    except (ConfigError, RuntimeError) as exc:
        logging.getLogger("pipeline").error("%s", exc)
        return 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
