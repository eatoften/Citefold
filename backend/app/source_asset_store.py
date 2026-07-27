import json
from datetime import datetime
from sqlite3 import Row

from .db import connect, ensure_db
from .job import utc_now
from .source_asset import SourceAsset, SourceAssetDetail, SourceUnit
from .trash_store import put_trash_item, remove_trash_item_for_entity


def _to_text(value: datetime) -> str:
    return value.isoformat()


def _from_text(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _row_to_asset(row: Row) -> SourceAssetDetail:
    keys = set(row.keys())
    return SourceAssetDetail(
        id=row["id"],
        course_id=row["course_id"],
        job_id=row["job_id"],
        asset_type=row["asset_type"],
        original_filename=row["original_filename"],
        stored_path=row["stored_path"],
        mime_type=row["mime_type"],
        size_bytes=row["size_bytes"],
        sha256=row["sha256"],
        extraction_status=row["extraction_status"],
        metadata=json.loads(row["metadata_json"]),
        error_message=row["error_message"],
        created_at=_from_text(row["created_at"]),
        updated_at=_from_text(row["updated_at"]),
        unit_count=row["unit_count"] if "unit_count" in keys else 0,
    )


def _row_to_unit(row: Row) -> SourceUnit:
    return SourceUnit(
        id=row["id"],
        asset_id=row["asset_id"],
        unit_type=row["unit_type"],
        ordinal=row["ordinal"],
        text=row["text"],
        locator=json.loads(row["locator_json"]),
        created_at=_from_text(row["created_at"]),
    )


def create_source_asset(asset: SourceAsset) -> None:
    ensure_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO source_assets (
                id, course_id, job_id, asset_type, original_filename,
                stored_path, mime_type, size_bytes, sha256,
                extraction_status, metadata_json, error_message,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset.id, asset.course_id, asset.job_id, asset.asset_type,
                asset.original_filename, asset.stored_path, asset.mime_type,
                asset.size_bytes, asset.sha256, asset.extraction_status,
                json.dumps(asset.metadata, ensure_ascii=False),
                asset.error_message, _to_text(asset.created_at),
                _to_text(asset.updated_at),
            ),
        )


def update_source_asset(asset: SourceAsset) -> None:
    ensure_db()
    with connect() as conn:
        conn.execute(
            """
            UPDATE source_assets
            SET extraction_status = ?, metadata_json = ?, error_message = ?,
                updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (
                asset.extraction_status,
                json.dumps(asset.metadata, ensure_ascii=False),
                asset.error_message,
                _to_text(asset.updated_at),
                asset.id,
            ),
        )


def get_source_asset(
    asset_id: str,
    *,
    include_deleted: bool = False,
) -> SourceAssetDetail | None:
    ensure_db()
    deleted_filter = (
        ""
        if include_deleted
        else """
            AND source_assets.deleted_at IS NULL
            AND courses.deleted_at IS NULL
        """
    )
    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT source_assets.*, COUNT(source_units.id) AS unit_count
            FROM source_assets
            INNER JOIN courses ON courses.id = source_assets.course_id
            LEFT JOIN source_units ON source_units.asset_id = source_assets.id
            WHERE source_assets.id = ?
              {deleted_filter}
            GROUP BY source_assets.id
            """,
            (asset_id,),
        ).fetchone()
    return _row_to_asset(row) if row is not None else None


def list_source_assets_for_course(
    course_id: str,
    *,
    include_deleted: bool = False,
) -> list[SourceAssetDetail]:
    ensure_db()
    deleted_filter = (
        ""
        if include_deleted
        else """
            AND source_assets.deleted_at IS NULL
            AND courses.deleted_at IS NULL
        """
    )
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT source_assets.*, COUNT(source_units.id) AS unit_count
            FROM source_assets
            INNER JOIN courses ON courses.id = source_assets.course_id
            LEFT JOIN source_units ON source_units.asset_id = source_assets.id
            WHERE source_assets.course_id = ?
              {deleted_filter}
            GROUP BY source_assets.id
            ORDER BY source_assets.updated_at DESC
            """,
            (course_id,),
        ).fetchall()
    return [_row_to_asset(row) for row in rows]


def replace_source_units(asset_id: str, units: list[SourceUnit]) -> None:
    ensure_db()
    with connect() as conn:
        conn.execute("DELETE FROM source_units WHERE asset_id = ?", (asset_id,))
        conn.executemany(
            """
            INSERT INTO source_units (
                id, asset_id, unit_type, ordinal, text, locator_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    unit.id, unit.asset_id, unit.unit_type, unit.ordinal,
                    unit.text, json.dumps(unit.locator, ensure_ascii=False),
                    _to_text(unit.created_at),
                )
                for unit in units
            ],
        )


def list_source_units_for_asset(asset_id: str) -> list[SourceUnit]:
    ensure_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT source_units.*
            FROM source_units
            INNER JOIN source_assets
                ON source_assets.id = source_units.asset_id
            INNER JOIN courses ON courses.id = source_assets.course_id
            WHERE source_units.asset_id = ?
              AND source_assets.deleted_at IS NULL
              AND courses.deleted_at IS NULL
            ORDER BY source_units.ordinal
            """,
            (asset_id,),
        ).fetchall()
    return [_row_to_unit(row) for row in rows]


def list_source_units_for_assets(asset_ids: list[str]) -> list[SourceUnit]:
    if not asset_ids:
        return []
    ensure_db()
    placeholders = ",".join("?" for _ in asset_ids)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT source_units.*
            FROM source_units
            INNER JOIN source_assets
                ON source_assets.id = source_units.asset_id
            INNER JOIN courses ON courses.id = source_assets.course_id
            WHERE source_units.asset_id IN ({placeholders})
              AND source_assets.deleted_at IS NULL
              AND courses.deleted_at IS NULL
            ORDER BY source_units.asset_id, source_units.ordinal
            """,
            asset_ids,
        ).fetchall()
    return [_row_to_unit(row) for row in rows]


def delete_source_asset(asset_id: str) -> bool:
    ensure_db()
    deleted_at = utc_now()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT course_id, original_filename
            FROM source_assets
            WHERE id = ? AND deleted_at IS NULL
            """,
            (asset_id,),
        ).fetchone()
        if row is None:
            return False
        cursor = conn.execute(
            """
            UPDATE source_assets SET deleted_at = ?, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (_to_text(deleted_at), _to_text(deleted_at), asset_id),
        )
        if cursor.rowcount != 1:
            return False
        put_trash_item(
            conn,
            entity_type="source_asset",
            entity_id=asset_id,
            course_id=row["course_id"],
            display_name=str(row["original_filename"]),
            deleted_at=deleted_at,
        )
    return cursor.rowcount > 0


def restore_source_asset(asset_id: str) -> bool:
    ensure_db()
    now = utc_now()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT source_assets.course_id
            FROM source_assets
            INNER JOIN courses ON courses.id = source_assets.course_id
            WHERE source_assets.id = ?
              AND source_assets.deleted_at IS NOT NULL
              AND courses.deleted_at IS NULL
            """,
            (asset_id,),
        ).fetchone()
        if row is None:
            return False
        cursor = conn.execute(
            """
            UPDATE source_assets
            SET deleted_at = NULL, updated_at = ?
            WHERE id = ? AND deleted_at IS NOT NULL
            """,
            (_to_text(now), asset_id),
        )
        if cursor.rowcount != 1:
            return False
        remove_trash_item_for_entity(
            conn,
            entity_type="source_asset",
            entity_id=asset_id,
        )
    return True


def purge_source_asset(
    asset_id: str,
    *,
    allow_parent_deleted: bool = False,
    preserve_trash_item: bool = False,
) -> bool:
    ensure_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT source_assets.deleted_at AS asset_deleted_at,
                   courses.deleted_at AS course_deleted_at
            FROM source_assets
            INNER JOIN courses ON courses.id = source_assets.course_id
            WHERE source_assets.id = ?
            """,
            (asset_id,),
        ).fetchone()
        if row is None or (
            row["asset_deleted_at"] is None
            and not (
                allow_parent_deleted
                and row["course_deleted_at"] is not None
            )
        ):
            return False
        conn.execute("DELETE FROM source_units WHERE asset_id = ?", (asset_id,))
        cursor = conn.execute(
            "DELETE FROM source_assets WHERE id = ?",
            (asset_id,),
        )
        if not preserve_trash_item:
            remove_trash_item_for_entity(
                conn,
                entity_type="source_asset",
                entity_id=asset_id,
            )
    return cursor.rowcount > 0


def move_source_assets_to_course(
    source_course_id: str,
    target_course_id: str,
) -> None:
    ensure_db()
    with connect() as conn:
        conn.execute(
            "UPDATE source_assets SET course_id = ? WHERE course_id = ?",
            (target_course_id, source_course_id),
        )


def clear_source_assets() -> None:
    ensure_db()
    with connect() as conn:
        conn.execute(
            "DELETE FROM trash_items WHERE entity_type = 'source_asset'"
        )
        conn.execute("DELETE FROM source_units")
        conn.execute("DELETE FROM source_assets")
