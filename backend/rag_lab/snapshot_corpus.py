from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from app.db import configure_db

from .corpus import snapshot_course_corpus
from .io import write_model_atomic


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Freeze a read-only RAG corpus snapshot from SQLite."
    )
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--snapshot-id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.database is not None:
        configure_db(args.database.resolve())
    snapshot = snapshot_course_corpus(
        args.course_id,
        snapshot_id=args.snapshot_id,
        database_path=args.database,
    )
    output_path = args.output.resolve()
    write_model_atomic(output_path, snapshot)
    print(
        json.dumps(
            {
                "output": str(output_path),
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_sha256": snapshot.snapshot_sha256,
                "source_database_sha256": snapshot.source_database_sha256,
                "card_count": len(snapshot.cards),
                "claim_count": sum(len(card.claims) for card in snapshot.cards),
                "evidence_count": sum(
                    len(claim.evidence)
                    for card in snapshot.cards
                    for claim in card.claims
                ),
                "relation_count": len(snapshot.relations),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
