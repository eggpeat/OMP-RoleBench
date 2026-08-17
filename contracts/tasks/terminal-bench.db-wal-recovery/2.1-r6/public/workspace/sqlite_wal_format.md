# SQLite Write-Ahead Log (WAL) Format Specification

A SQLite WAL (Write-Ahead Log) file begins with a 32-byte header followed by zero or more frames.

## 32-Byte WAL Header Format

All multi-byte integer quantities in the WAL header and frame headers are stored in big-endian byte order unless specified otherwise.

| Offset (Bytes) | Size (Bytes) | Field Name | Description |
|---|---|---|---|
| 0 | 4 | Magic Number | `0x377f0682` (big-endian checksum format) or `0x377f0683` (little-endian checksum format). Expected standard magic: `0x377f0682`. |
| 4 | 4 | File Format Version | WAL format version number (typically `3007000` / `0x002de218`). |
| 8 | 4 | Database Page Size | Database page size in bytes (e.g. 4096 / `0x00001000`). |
| 12 | 4 | Checkpoint Sequence Number | Sequence number incremented on each checkpoint (starts at 0). |
| 16 | 4 | Salt-1 | Random integer 1 generated when WAL file is initialized. |
| 20 | 4 | Salt-2 | Random integer 2 incremented or regenerated on checkpoints. |
| 24 | 4 | Checksum-1 | First 32-bit checksum of the 24-byte header prefix. |
| 28 | 4 | Checksum-2 | Second 32-bit checksum of the 24-byte header prefix. |

## WAL Frame Structure

Immediately following the 32-byte WAL header are sequential WAL frames. Each frame consists of a 24-byte frame header followed by `page_size` bytes of page content.

### 24-Byte Frame Header Format

| Offset (Bytes) | Size (Bytes) | Field Name | Description |
|---|---|---|---|
| 0 | 4 | Page Number | 1-based page number within the database file. |
| 4 | 4 | Database Size (Pages) | Commit record: size of database in pages after this transaction commits (or 0 for non-commit frames). |
| 8 | 4 | Salt-1 | Must match Salt-1 from WAL header. |
| 12 | 4 | Salt-2 | Must match Salt-2 from WAL header. |
| 16 | 4 | Frame Checksum-1 | Cumulative checksum 1 over frame header and page data. |
| 20 | 4 | Frame Checksum-2 | Cumulative checksum 2 over frame header and page data. |
