#!/usr/bin/env python3
"""Map PolyTracker's control-flow-dependent offsets to TxnFixture fields."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from polytracker import PolyTrackerTrace
from polytracker.taint_dag import TDRangeNode, TDSourceNode, TDUnionNode


@dataclass(frozen=True)
class Field:
    name: str
    kind: str
    message: Optional[str] = None
    repeated: bool = False


@dataclass(frozen=True)
class Region:
    path: str
    kind: str
    start: int
    end: int


SCHEMAS: dict[str, dict[int, Field]] = {
    "TxnFixture": {
        1: Field("metadata", "opaque"),
        2: Field("input", "message", "TxnContext"),
        3: Field("output", "opaque"),
    },
    "TxnContext": {
        1: Field("tx", "message", "SanitizedTransaction"),
        2: Field("account_shared_data", "message", "AcctState", True),
        6: Field("bank", "message", "TxnBank"),
    },
    "SanitizedTransaction": {
        1: Field("message", "message", "TransactionMessage"),
        2: Field("message_hash", "bytes"),
        4: Field("signatures", "bytes", repeated=True),
    },
    "TransactionMessage": {
        2: Field("header", "message", "MessageHeader"),
        3: Field("account_keys", "bytes", repeated=True),
        5: Field("recent_blockhash", "bytes"),
        6: Field("instructions", "message", "CompiledInstruction", True),
        7: Field(
            "address_table_lookups", "message", "MessageAddressTableLookup", True
        ),
        8: Field("version", "varint"),
        9: Field("v1_config", "message", "TransactionConfig"),
    },
    "MessageHeader": {
        1: Field("num_required_signatures", "varint"),
        2: Field("num_readonly_signed_accounts", "varint"),
        3: Field("num_readonly_unsigned_accounts", "varint"),
    },
    "CompiledInstruction": {
        1: Field("program_id_index", "varint"),
        2: Field("accounts", "packed_varint", repeated=True),
        3: Field("data", "bytes"),
    },
    "MessageAddressTableLookup": {
        1: Field("account_key", "bytes"),
        2: Field("writable_indexes", "packed_varint", repeated=True),
        3: Field("readonly_indexes", "packed_varint", repeated=True),
    },
    "TransactionConfig": {
        1: Field("priority_fee", "varint"),
        2: Field("compute_unit_limit", "varint"),
        3: Field("loaded_accounts_data_size_limit", "varint"),
        4: Field("heap_size", "varint"),
    },
    "AcctState": {
        1: Field("address", "bytes"),
        2: Field("lamports", "varint"),
        4: Field("executable", "varint"),
        6: Field("owner", "bytes"),
        8: Field("data_hash", "fixed64"),
        9: Field("data", "bytes"),
    },
    "TxnBank": {
        1: Field("blockhash_queue", "message", "BlockhashQueueEntry", True),
        2: Field("rbh_lamports_per_signature", "varint"),
        3: Field("fee_rate_governor", "message", "FeeRateGovernor"),
        4: Field("total_epoch_stake", "varint"),
        7: Field("features", "message", "FeatureSet"),
    },
    "FeatureSet": {1: Field("features", "packed_fixed64", repeated=True)},
    "FeeRateGovernor": {
        1: Field("target_lamports_per_signature", "varint"),
        2: Field("target_signatures_per_slot", "varint"),
        3: Field("min_lamports_per_signature", "varint"),
        4: Field("max_lamports_per_signature", "varint"),
        5: Field("burn_percent", "varint"),
    },
    "BlockhashQueueEntry": {
        1: Field("blockhash", "bytes"),
        2: Field("lamports_per_signature", "varint"),
    },
}


def read_varint(data: bytes, pos: int, limit: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while pos < limit and shift < 70:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, pos
        shift += 7
    raise ValueError(f"invalid varint ending at byte {pos}")


def parse_message(
    data: bytes, schema_name: str, start: int, end: int, prefix: str
) -> list[Region]:
    schema = SCHEMAS[schema_name]
    regions: list[Region] = []
    occurrences: defaultdict[int, int] = defaultdict(int)
    pos = start

    while pos < end:
        key, after_key = read_varint(data, pos, end)
        number, wire = key >> 3, key & 7
        spec = schema.get(number)

        if wire == 0:
            value_start = after_key
            _, value_end = read_varint(data, value_start, end)
        elif wire == 1:
            value_start, value_end = after_key, after_key + 8
        elif wire == 2:
            length, value_start = read_varint(data, after_key, end)
            value_end = value_start + length
        elif wire == 5:
            value_start, value_end = after_key, after_key + 4
        else:
            raise ValueError(f"unsupported wire type {wire} at byte {pos}")
        if value_end > end:
            raise ValueError(f"field {number} extends past message at byte {pos}")

        if spec is not None:
            index = occurrences[number]
            occurrences[number] += 1
            suffix = (
                f"[{index}]"
                if spec.repeated and not spec.kind.startswith("packed_")
                else ""
            )
            path = f"{prefix}.{spec.name}{suffix}" if prefix else spec.name + suffix

            if spec.kind == "message":
                if wire != 2 or spec.message is None:
                    raise ValueError(f"invalid encoding for {path} at byte {pos}")
                regions.extend(
                    parse_message(data, spec.message, value_start, value_end, path)
                )
            elif spec.kind == "packed_varint":
                element = value_start
                packed_index = 0
                while element < value_end:
                    _, element_end = read_varint(data, element, value_end)
                    regions.append(
                        Region(f"{path}[{packed_index}]", "varint", element, element_end)
                    )
                    element = element_end
                    packed_index += 1
            elif spec.kind == "packed_fixed64":
                if (value_end - value_start) % 8:
                    raise ValueError(f"invalid packed fixed64 field {path}")
                for packed_index, element in enumerate(
                    range(value_start, value_end, 8)
                ):
                    regions.append(
                        Region(
                            f"{path}[{packed_index}]", "fixed64", element, element + 8
                        )
                    )
            else:
                regions.append(Region(path, spec.kind, value_start, value_end))

        pos = value_end

    return regions


def control_flow_offsets(trace_path: Path, fixture_path: Path) -> set[int]:
    trace = PolyTrackerTrace.load(trace_path)
    fixture = fixture_path.resolve()
    headers = trace.tdfile.fd_headers
    entries = list(headers.values() if isinstance(headers, dict) else headers)

    def is_fixture_source(path: Path) -> bool:
        try:
            return path.resolve() == fixture
        except OSError:
            return path == fixture_path

    fixture_recorded = any(is_fixture_source(Path(path)) for path, _ in entries)
    offsets: set[int] = set()
    matched_sources: set[str] = set()
    roots: list[int] = []

    # A branch commonly depends on a union/range label rather than a source
    # label directly.  Walk every marked label back to its source leaves.
    for label in range(1, trace.tdfile.label_count):
        node = trace.tdfile.decode_node(label)
        if node.affects_control_flow:
            roots.append(label)

    seen: set[int] = set()
    pending = roots
    while pending:
        label = pending.pop()
        if label in seen:
            continue
        seen.add(label)
        node = trace.tdfile.decode_node(label)
        if isinstance(node, TDUnionNode):
            pending.extend((node.left, node.right))
            continue
        if isinstance(node, TDRangeNode):
            pending.extend(range(node.first, node.last + 1))
            continue
        if not isinstance(node, TDSourceNode):
            continue
        source_path = Path(trace.tdfile.fd_headers[node.idx][0])
        try:
            matches = source_path.resolve() == fixture
        except OSError:
            matches = source_path == fixture_path
        if matches:
            matched_sources.add(str(source_path))
            offsets.add(node.offset)

    if not matched_sources:
        if fixture_recorded:
            return offsets
        known = sorted({str(path) for path, _ in entries})
        raise RuntimeError(
            f"fixture was not recorded as a trace source; trace sources: {known}"
        )
    return offsets


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--all", action="store_true", help="include unused leaf fields")
    parser.add_argument(
        "--prefix",
        action="append",
        default=[],
        help="only print field paths beginning with this prefix (repeatable)",
    )
    parser.add_argument(
        "--ignore-prefix",
        action="append",
        default=[],
        help="exclude field paths beginning with this prefix (repeatable)",
    )
    args = parser.parse_args()

    data = args.fixture.read_bytes()
    regions = parse_message(data, "TxnFixture", 0, len(data), "")
    affected = control_flow_offsets(args.trace, args.fixture)
    input_hits: set[int] = set()
    affected_input_fields = 0
    for region in regions:
        if not region.path.startswith("input."):
            continue
        if any(region.path.startswith(prefix) for prefix in args.ignore_prefix):
            continue
        hits = affected.intersection(range(region.start, region.end))
        input_hits.update(hits)
        affected_input_fields += bool(hits)

    print(f"fixture_bytes={len(data)}")
    print(f"control_flow_dependent_fixture_bytes={len(affected)}")
    print(f"control_flow_dependent_input_value_bytes={len(input_hits)}")
    print(f"affected_input_leaf_fields={affected_input_fields}")
    print("field\tkind\trange\taffected/sample")
    for region in regions:
        if not region.path.startswith("input."):
            continue
        if any(region.path.startswith(prefix) for prefix in args.ignore_prefix):
            continue
        if args.prefix and not any(
            region.path.startswith(prefix) for prefix in args.prefix
        ):
            continue
        hits = sorted(affected.intersection(range(region.start, region.end)))
        if not hits and not args.all:
            continue
        sample = ",".join(map(str, hits[:12]))
        if len(hits) > 12:
            sample += ",..."
        print(
            f"{region.path}\t{region.kind}\t[{region.start},{region.end})\t"
            f"{len(hits)}/{region.end - region.start}: {sample}"
        )


if __name__ == "__main__":
    main()
