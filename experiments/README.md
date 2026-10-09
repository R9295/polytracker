# Agave compatibility harness

This experiment runs a Firedancer transaction fixture through Agave's
`solana_svm_conformance::txn::execute_txn_proto` and maps input bytes that
affect instrumented branches back to protobuf fields.

## Set up PolyTracker and Agave

Build PolyTracker with LLVM/Clang 23 as described in the root README. The
harness expects these artifacts:

```text
build/llvm23-rust/libPolytrackerPass.so
build/llvm23-runtime-make/polytracker/src/libPolytracker.a
build/llvm23-runtime-make/polytracker/src/compiler-rt/lib/linux/libclang_rt.dfsan-x86_64.a
```

The pass must be linked against the LLVM library used by Rust. Check both
versions before building the harness:

```bash
rustc +nightly --version --verbose | grep 'LLVM version'
ldd build/llvm23-rust/libPolytrackerPass.so | grep libLLVM
```

Both must report LLVM 23.x. Then fetch the pinned inputs:

```bash
git clone https://github.com/anza-xyz/agave.git experiments/agave
git -C experiments/agave checkout 39f386aadaf9a551eb690997e1d58769f165ea22
git -C experiments/agave apply --unidiff-zero ../agave-polytracker-runner/agave-svm-conformance.patch

git clone --filter=blob:none --sparse https://github.com/firedancer-io/test-vectors.git experiments/test-vectors
git -C experiments/test-vectors checkout 0074649d2720d237546dd5f30f51c0be31477eb1
git -C experiments/test-vectors sparse-checkout set txn/fixtures
```

The Agave patch disables its optional `cdylib`; PolyTracker's static DFSan
runtime cannot be linked into that shared library.

## Instrument and run

From the repository root:

```bash
experiments/agave-polytracker-runner/build-instrumented.sh

INPUT=experiments/test-vectors/txn/fixtures/0103115bd3ec4f27fcf4dbd7cee22a530e68f797_2135631.fix
BIN=experiments/agave/dev-bins/target-polytracker/x86_64-unknown-linux-gnu/debug/agave-polytracker-runner
POLYDB=runtime.tdag "$BIN" "$INPUT"
```

Run the binary directly after building. Setting `POLYDB` on `cargo run` can
let an instrumented Cargo subprocess overwrite the trace. Use a new trace name
for each run.

Pass `--check` when you also want the harness to compare Agave's result with
the fixture's expected output; a mismatch exits with status 1.

The build instruments data propagation throughout the dependency graph and
records branch dependencies only in these crates:

```text
solana-program-runtime, solana-svm, solana-accounts-db, solana-runtime,
solana-runtime-transaction, solana-transaction-context
```

Override that set with `POLYTRACKER_TAINT_CRATE_ALLOWLIST` if needed.

## Map trace bytes to protobuf fields

Create the analysis environment once, then analyze a trace:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python experiments/agave-polytracker-runner/analyze_fields.py \
  runtime.tdag "$INPUT" --ignore-prefix input.bank.features
```

Each output row gives the protobuf field path, wire value kind, half-open byte
range in the fixture, and `affected bytes/field bytes` followed by a sample of
affected offsets. Use `--all` to include unaffected fields, `--prefix PATH` to
select fields, and repeat `--ignore-prefix PATH` to exclude fields.
