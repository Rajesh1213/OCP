"""ocp-trainer CLI — export, finetune, register, stats."""
from __future__ import annotations

import argparse
import asyncio
import sys


def _cmd_stats(args: argparse.Namespace) -> None:
    from ocp_trainer.exporter import print_stats
    print_stats(args.db)


def _cmd_export(args: argparse.Namespace) -> None:
    from ocp_trainer.exporter import export_dataset

    count = asyncio.run(
        export_dataset(
            db_path=args.db,
            output_path=args.output,
            fmt=args.format,
            workspace_id=args.workspace_id,
            since=args.since,
            only_completed=not args.include_incomplete,
        )
    )
    print(f"Exported {count} records → {args.output}")


def _cmd_finetune(args: argparse.Namespace) -> None:
    if args.backend == "openai":
        from ocp_trainer.finetune import finetune_openai
        job_id = finetune_openai(
            dataset_path=args.dataset,
            base_model=args.model,
            suffix=args.suffix,
        )
        print(f"Job ID: {job_id}")
    else:
        from ocp_trainer.finetune import finetune_local
        out = finetune_local(
            dataset_path=args.dataset,
            base_model=args.model,
            output_dir=args.output_dir,
            max_steps=args.max_steps,
            fmt=args.format,
        )
        print(f"Adapter saved → {out}")


def _cmd_register(args: argparse.Namespace) -> None:
    from ocp_trainer.finetune import register_ollama
    register_ollama(adapter_path=args.adapter, model_name=args.name)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ocp-trainer",
        description="OCP fine-tuning pipeline — export dataset, train, register model",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── stats ──────────────────────────────────────────────────────
    p_stats = sub.add_parser("stats", help="Show token savings and trace statistics")
    p_stats.add_argument("--db", default="ocp.db", help="Path to OCP SQLite database")

    # ── export ─────────────────────────────────────────────────────
    p_export = sub.add_parser("export", help="Export prompt traces as JSONL training dataset")
    p_export.add_argument("--db", default="ocp.db", help="Path to OCP SQLite database")
    p_export.add_argument("--output", default="ocp_dataset.jsonl", help="Output JSONL file path")
    p_export.add_argument(
        "--format", choices=["alpaca", "chatml", "openai"], default="alpaca",
        help="Training data format (default: alpaca)"
    )
    p_export.add_argument("--workspace-id", dest="workspace_id", help="Filter by workspace")
    p_export.add_argument("--since", help="Only export traces after this ISO-8601 date")
    p_export.add_argument(
        "--include-incomplete", action="store_true",
        help="Include traces without a recorded result"
    )

    # ── finetune ───────────────────────────────────────────────────
    p_ft = sub.add_parser("finetune", help="Run LoRA fine-tuning on the exported dataset")
    p_ft.add_argument("--dataset", required=True, help="Path to JSONL dataset")
    p_ft.add_argument(
        "--backend", choices=["local", "openai"], default="local",
        help="local = Unsloth/LoRA (requires GPU), openai = OpenAI fine-tuning API"
    )
    p_ft.add_argument(
        "--model", default="unsloth/llama-3.2-3b-instruct",
        help="Base model (local) or fine-tuning model (openai)"
    )
    p_ft.add_argument("--output-dir", default="./ocp-optimizer-lora", help="Adapter output directory")
    p_ft.add_argument("--max-steps", type=int, default=200, help="Training steps (local only)")
    p_ft.add_argument("--format", choices=["alpaca", "chatml"], default="alpaca", help="Dataset format")
    p_ft.add_argument("--suffix", default="ocp-optimizer", help="Model suffix (openai only)")

    # ── register ───────────────────────────────────────────────────
    p_reg = sub.add_parser("register", help="Register fine-tuned adapter as an Ollama model")
    p_reg.add_argument("--adapter", required=True, help="Path to LoRA adapter directory")
    p_reg.add_argument("--name", default="ocp-optimizer", help="Ollama model name")

    args = parser.parse_args()

    try:
        if args.command == "stats":
            _cmd_stats(args)
        elif args.command == "export":
            _cmd_export(args)
        elif args.command == "finetune":
            _cmd_finetune(args)
        elif args.command == "register":
            _cmd_register(args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
