#!/usr/bin/env bash
set -euo pipefail

harness_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$harness_dir/../.." && pwd)
agave_dir="$repo_root/experiments/agave"
target_dir="$agave_dir/dev-bins/target-polytracker"
pass="$repo_root/build/llvm23-rust/libPolytrackerPass.so"
runtime="$repo_root/build/llvm23-runtime-make/polytracker/src/libPolytracker.a"
dfsan="$repo_root/build/llvm23-runtime-make/polytracker/src/compiler-rt/lib/linux/libclang_rt.dfsan-x86_64.a"
toolchain=${RUST_TOOLCHAIN:-nightly}

for required in "$agave_dir/svm-conformance/Cargo.toml" "$pass" "$runtime" "$dfsan"; do
    if [[ ! -e "$required" ]]; then
        echo "missing required file: $required" >&2
        exit 1
    fi
done

llvm_version=$(rustc "+$toolchain" --version --verbose | sed -n 's/^LLVM version: //p')
if [[ "$llvm_version" != 23.* ]]; then
    echo "rustc +$toolchain uses LLVM $llvm_version; LLVM 23.x is required" >&2
    exit 1
fi

rustflags=(
    "-Zllvm-plugins=$pass"
    "-Cpasses=pt-taint,pt-dfsan,pt-rm-fn-attr"
    "-Cdebuginfo=1"
    "-Cmetadata=polytracker-agave-runtime-v5"
    "-Clink-arg=-Wl,--allow-multiple-definition"
    "-Clink-arg=-Wl,--start-group"
    "-Clink-arg=$runtime"
    "-Clink-arg=$dfsan"
    "-Clink-arg=-lstdc++"
    "-Clink-arg=-lm"
    "-Clink-arg=-ltinfo"
    "-Clink-arg=-ldl"
    "-Clink-arg=-lpthread"
    "-Clink-arg=-Wl,--end-group"
)
printf -v encoded_rustflags '%s\037' "${rustflags[@]}"

export CARGO_TARGET_DIR="$target_dir"
export CARGO_ENCODED_RUSTFLAGS="${encoded_rustflags%$'\037'}"
export POLYTRACKER_TAINT_IGNORE_LIST="$repo_root/polytracker/custom_abi/polytracker_abilist.txt"
export POLYTRACKER_DFSAN_ABILIST="$repo_root/polytracker/custom_abi/dfsan_abilist.txt"
export POLYTRACKER_TAINT_CRATE_ALLOWLIST="${POLYTRACKER_TAINT_CRATE_ALLOWLIST:-solana_program_runtime,solana_svm,solana_accounts_db,solana_runtime,solana_runtime_transaction,solana_transaction_context}"

exec cargo "+$toolchain" build \
    --manifest-path "$harness_dir/Cargo.toml" \
    --target x86_64-unknown-linux-gnu \
    -Zbuild-std=std,panic_unwind \
    -j "${BUILD_JOBS:-16}" \
    "$@"
