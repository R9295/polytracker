use {
    prost::Message,
    protosol::protos::TxnFixture,
    std::{env, path::PathBuf, process::ExitCode},
};

unsafe extern "C" {
    fn __polytracker_set_control_flow_logging(enabled: u8);
}

fn main() -> ExitCode {
    // Keep parsing dependencies out of the trace; labels still propagate
    // through decoding while control-flow marking is disabled.
    unsafe { __polytracker_set_control_flow_logging(0) };

    let mut fixture_path = None;
    let mut check_expected = false;

    for argument in env::args_os().skip(1) {
        if argument == "--check" {
            check_expected = true;
        } else if fixture_path.replace(PathBuf::from(argument)).is_some() {
            eprintln!("usage: agave-polytracker-runner [--check] <fixture.fix>");
            return ExitCode::from(2);
        }
    }

    let Some(fixture_path) = fixture_path else {
        eprintln!("usage: agave-polytracker-runner [--check] <fixture.fix>");
        return ExitCode::from(2);
    };

    // PolyTracker's read interceptor labels this buffer by fixture byte offset.
    let fixture_bytes = match std::fs::read(&fixture_path) {
        Ok(bytes) => bytes,
        Err(error) => {
            eprintln!("failed to read {}: {error}", fixture_path.display());
            return ExitCode::FAILURE;
        }
    };
    let fixture = match TxnFixture::decode(fixture_bytes.as_slice()) {
        Ok(fixture) => fixture,
        Err(error) => {
            eprintln!("failed to decode {}: {error}", fixture_path.display());
            return ExitCode::FAILURE;
        }
    };
    let Some(context) = fixture.input else {
        eprintln!("fixture has no transaction context");
        return ExitCode::FAILURE;
    };

    unsafe { __polytracker_set_control_flow_logging(1) };
    let actual = solana_svm_conformance::txn::execute_txn_proto(context);
    unsafe { __polytracker_set_control_flow_logging(0) };
    println!("fixture={}", fixture_path.display());
    println!("executed={}", actual.executed);
    println!("txn_error={}", actual.txn_error);
    println!("instruction_error={}", actual.instruction_error);
    println!("executed_units={}", actual.executed_units);
    println!("modified_accounts={}", actual.modified_accounts.len());

    if check_expected {
        let Some(expected) = fixture.output else {
            eprintln!("fixture has no expected output");
            return ExitCode::FAILURE;
        };
        if actual != expected {
            eprintln!("fixture output does not match Agave");
            return ExitCode::FAILURE;
        }
        println!("fixture_match=true");
    }

    ExitCode::SUCCESS
}
