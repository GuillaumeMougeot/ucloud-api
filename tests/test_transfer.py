"""WEBSOCKET_V2 frame encoding/decoding (pure, no network)."""

from __future__ import annotations

import struct

from ucloud_api.transfer import (
    _OP_CHUNK,
    _OP_COMPLETED,
    _OP_LISTING,
    _OP_OK,
    _OP_SKIP,
    _chunk_frame,
    _listing_frame,
    _parse_server_frame,
)


def test_listing_frame_layout() -> None:
    frame = _listing_frame(1, 150000, 1_700_000_000_000, "sub/file.txt")
    assert frame[0] == _OP_LISTING
    file_id = struct.unpack_from(">i", frame, 1)[0]
    size = struct.unpack_from(">q", frame, 5)[0]
    modified = struct.unpack_from(">q", frame, 13)[0]
    path_len = struct.unpack_from(">i", frame, 21)[0]
    path = frame[25 : 25 + path_len].decode()
    assert (file_id, size, modified, path) == (1, 150000, 1_700_000_000_000, "sub/file.txt")


def test_listing_frame_empty_path() -> None:
    frame = _listing_frame(1, 0, 0, "")
    # opcode + i32 + i64 + i64 + i32(len=0) == 25 bytes, no path bytes
    assert len(frame) == 25
    assert struct.unpack_from(">i", frame, 21)[0] == 0


def test_chunk_frame_layout() -> None:
    frame = _chunk_frame(7, b"payload")
    assert frame[0] == _OP_CHUNK
    assert struct.unpack_from(">i", frame, 1)[0] == 7
    assert frame[5:] == b"payload"


def test_parse_server_frame_handles_multiple_messages() -> None:
    frame = bytes([_OP_OK]) + struct.pack(">i", 1) + bytes([_OP_COMPLETED]) + struct.pack(">i", 3)
    assert _parse_server_frame(frame) == [(_OP_OK, 1), (_OP_COMPLETED, 3)]


def test_parse_server_frame_skip() -> None:
    frame = bytes([_OP_SKIP]) + struct.pack(">i", 42)
    assert _parse_server_frame(frame) == [(_OP_SKIP, 42)]


def test_parse_server_frame_stops_on_unknown_opcode() -> None:
    frame = bytes([99]) + struct.pack(">i", 1)
    assert _parse_server_frame(frame) == []
