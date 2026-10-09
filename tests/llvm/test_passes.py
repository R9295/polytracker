"""LLVM IR regressions, runnable with unittest, pytest, or CTest."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class LLVM23PassTests(unittest.TestCase):
    def setUp(self):
        self.opt = os.environ.get("LLVM_OPT", "opt")
        compiler_dir = Path(
            os.environ.get("COMPILER_DIR", "/polytracker-install/share/polytracker")
        )
        self.plugin = Path(
            os.environ.get(
                "POLYTRACKER_PASS", compiler_dir / "pass/libPolytrackerPass.so"
            )
        )
        self.abi = (
            Path(__file__).resolve().parents[2]
            / "polytracker/custom_abi/dfsan_abilist.txt"
        )
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        if not self.plugin.is_file() or not shutil.which(self.opt):
            self.fail("Build the LLVM 23 pass and set POLYTRACKER_PASS and LLVM_OPT")

    def instrument(self, ir, passes="pt-dfsan,pt-rm-fn-attr", extra=()):
        source = 'target triple = "x86_64-unknown-linux-gnu"\n' + ir
        result = subprocess.run(
            [
                self.opt,
                "-load",
                str(self.plugin),
                "-load-pass-plugin",
                str(self.plugin),
                f"-passes={passes}",
                f"-pt-dfsan-abilist={self.abi}",
                "-verify-each",
                "-S",
                "-o",
                "-",
                *extra,
            ],
            input=source,
            text=True,
            capture_output=True,
            cwd=self.work.name,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_opaque_aggregate_and_vector_memory(self):
        output = self.instrument("""
            define { i32, ptr } @aggregate(ptr %src, ptr %dst) {
              %a = load { i32, ptr }, ptr %src, align 8
              store { i32, ptr } %a, ptr %dst, align 8
              ret { i32, ptr } %a
            }
            define <4 x i32> @vector(ptr %src, ptr %dst) {
              %v = load <4 x i32>, ptr %src, align 16
              store <4 x i32> %v, ptr %dst, align 16
              ret <4 x i32> %v
            }
        """)
        self.assertIn("@__dfsan_shadow_width_bits = weak_odr constant i32 32", output)
        self.assertIn("@__dfsan_arg_tls", output)
        self.assertIn("@__dfsan_retval_tls", output)
        self.assertIn("@__dfsan_union", output)

    def test_main_without_arguments(self):
        output = self.instrument(
            "define i32 @main() { ret i32 0 }", "pt-taint,pt-dfsan,pt-rm-fn-attr"
        )
        self.assertIn("define i32 @main()", output)
        self.assertNotIn("call void @__polytracker_taint_argv", output)

    def test_main_with_arguments(self):
        output = self.instrument(
            "define i32 @main(i32 %argc, ptr %argv) { ret i32 %argc }",
            "pt-taint,pt-dfsan,pt-rm-fn-attr",
        )
        self.assertIn("call void @__polytracker_taint_argv", output)

    def test_indirect_pthread_callback(self):
        output = self.instrument("""
            declare i32 @pthread_create(ptr, ptr, ptr, ptr)
            define i32 @start(ptr %thread, ptr %callback, ptr %data) {
              %r = call i32 @pthread_create(ptr %thread, ptr null, ptr %callback, ptr %data)
              ret i32 %r
            }
        """)
        self.assertIn(
            '@__dfsw_pthread_create(ptr %thread, ptr null, ptr @"dfst2$pthread_create", ptr %callback',
            output,
        )
        self.assertIn('define linkonce_odr ptr @"dfst2$pthread_create"', output)

    def test_libc_callbacks(self):
        # signal's custom wrapper is opt-in in the shipped ABI list.
        signal_abi = Path(self.work.name) / "signal.abilist"
        signal_abi.write_text("fun:signal=custom\n")
        output = self.instrument(
            """
            declare i32 @dl_iterate_phdr(ptr, ptr)
            declare ptr @signal(i32, ptr)
            define i32 @callbacks(ptr %cb, ptr %data, ptr %handler) {
              %a = call i32 @dl_iterate_phdr(ptr %cb, ptr %data)
              %b = call ptr @signal(i32 2, ptr %handler)
              ret i32 %a
            }
        """,
            extra=(f"-pt-dfsan-abilist={signal_abi}",),
        )
        self.assertIn('ptr @"dfst0$dl_iterate_phdr", ptr %cb', output)
        self.assertIn('ptr @"dfst1$signal", ptr %handler', output)

    def test_memory_effects_do_not_survive_instrumentation(self):
        output = self.instrument("""
            define i32 @sum(i32 %a, i32 %b) memory(none) nosync nofree speculatable {
              %v = add i32 %a, %b
              ret i32 %v
            }
            define i32 @indirect(ptr %f, i32 %a) {
              %v = call i32 %f(i32 %a) memory(none) nosync nofree
              ret i32 %v
            }
        """)
        self.assertNotIn("memory(none)", output)
        self.assertNotIn("speculatable", output)
        self.assertNotIn("nosync", output)
        self.assertNotIn("nofree", output)

    def test_memcpy_alignment(self):
        output = self.instrument(
            """
            declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)
            define void @copy(ptr %dst, ptr %src, i64 %len) {
              call void @llvm.memcpy.p0.p0.i64(ptr align 8 %dst, ptr align 8 %src, i64 %len, i1 false)
              ret void
            }
        """,
            extra=("-pt-dfsan-preserve-alignment",),
        )
        self.assertIn("ptr align 32", output)

    def test_variadic_custom_wrapper(self):
        output = self.instrument("""
            declare i32 @sprintf(ptr, ptr, ...)
            define i32 @format(ptr %out, ptr %fmt, i32 %value) {
              %n = call i32 (ptr, ptr, ...) @sprintf(ptr %out, ptr %fmt, i32 %value)
              ret i32 %n
            }
        """)
        self.assertIn("@__dfsw_sprintf", output)
        self.assertIn("%labelva = alloca [1 x i32]", output)

    def test_invoke_memory_effects(self):
        output = self.instrument("""
            declare i32 @__gxx_personality_v0(...)
            declare i32 @callee(i32) memory(none)
            define i32 @caller(i32 %value) personality ptr @__gxx_personality_v0 {
              %n = invoke i32 @callee(i32 %value) memory(none)
                  to label %done unwind label %exception
            done:
              ret i32 %n
            exception:
              %landing = landingpad { ptr, i32 } cleanup
              resume { ptr, i32 } %landing
            }
        """)
        self.assertNotIn("memory(none)", output)

    def test_tracing_and_optimization(self):
        output = self.instrument(
            """
            define i32 @main(i32 %argc, ptr %argv) {
              %c = icmp eq i32 %argc, 1
              br i1 %c, label %yes, label %no
            yes:
              ret i32 0
            no:
              ret i32 1
            }
        """,
            "pt-tcf,default<O2>,pt-taint,pt-ftrace,pt-dfsan,pt-rm-fn-attr,default<O2>",
        )
        self.assertIn("@__dfsw___polytracker_log_tainted_control_flow", output)
        self.assertIn("@__polytracker_log_func_entry", output)


if __name__ == "__main__":
    unittest.main()
