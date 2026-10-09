/*
 * Copyright (c) 2022-present, Trail of Bits, Inc.
 * All rights reserved.
 *
 * This source code is licensed in accordance with the terms specified in
 * the LICENSE file found in the root directory of this source tree.
 */

#include "polytracker/passes/remove_fn_attr.h"

namespace polytracker {

static bool isInstrumented(llvm::StringRef name) {
  return name.starts_with("dfs$") || name.starts_with("dfsw$") ||
         name.starts_with("dfso$") || name.starts_with("__dfsw_") ||
         name.starts_with("__dfso_");
}

void RemoveFnAttrsPass::visitCallBase(llvm::CallBase &ci) {
  auto fn{ci.getCalledFunction()};
  // Indirect calls use the instrumented ABI too. Shadow accesses and logging
  // invalidate memory effects inferred before instrumentation.
  if (!fn || isInstrumented(fn->getName())) {
    ci.removeFnAttr(llvm::Attribute::Memory);
    ci.removeFnAttr(llvm::Attribute::Speculatable);
    ci.removeFnAttr(llvm::Attribute::NoSync);
    ci.removeFnAttr(llvm::Attribute::NoFree);
  }
}

llvm::PreservedAnalyses
RemoveFnAttrsPass::run(llvm::Module &mod, llvm::ModuleAnalysisManager &mam) {
  for (auto &fn : mod) {
    auto fname{fn.getName()};
    if (isInstrumented(fname)) {
      fn.removeFnAttr(llvm::Attribute::Memory);
      fn.removeFnAttr(llvm::Attribute::Speculatable);
      fn.removeFnAttr(llvm::Attribute::NoSync);
      fn.removeFnAttr(llvm::Attribute::NoFree);
    }
    visit(fn);
  }
  return llvm::PreservedAnalyses::none();
}

} // namespace polytracker
