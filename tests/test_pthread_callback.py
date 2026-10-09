from pathlib import Path

import pytest

from polytracker.taint_dag import TDSourceNode


@pytest.fixture
def stdout_sink(monkeypatch):
    monkeypatch.setenv("POLYTRACKER_STDOUT_SINK", "1")


@pytest.mark.program_trace("test_pthread_callback.c")
def test_callback_preserves_taint(stdout_sink, program_trace):
    tdfile = program_trace.tdfile
    paths = [header[0] for header in tdfile.fd_headers]
    writes = [sink for sink in tdfile.sinks if paths[sink.fdidx] == Path("/dev/stdout")]
    assert len(writes) == 1
    source = tdfile.decode_node(writes[0].label)
    assert isinstance(source, TDSourceNode)
    assert source.offset == 0
