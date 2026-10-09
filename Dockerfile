# Build base image
FROM ubuntu:jammy AS base

LABEL org.opencontainers.image.authors="evan.sultanik@trailofbits.com"

ARG BUILD_TYPE="Release"
ARG BUILD_JOBS=8

# Install base build dependencies via apt
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get -y update && apt-get -y install \
  ca-certificates                           \
  wget                                      \
  ninja-build                               \
  python3-pip                               \
  python3-dev                               \
  golang                                    \
  cmake                                     \
  git                                       \
  file

# Use one LLVM major for the compiler, bitcode tools, and pass headers.
RUN wget -qO /etc/apt/trusted.gpg.d/apt.llvm.org.asc https://apt.llvm.org/llvm-snapshot.gpg.key && \
    echo "deb https://apt.llvm.org/jammy/ llvm-toolchain-jammy-23 main" > /etc/apt/sources.list.d/llvm.list && \
    apt-get -y update && apt-get -y install clang-23 llvm-23-dev llvm-23-tools

# Install python dependencies via pip
RUN pip3 install pytest

# Install symlinks to clang and llvm bitcode tools
ENV PATH=/usr/lib/llvm-23/bin:$PATH
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3 10

# Install gllvm for builds with bitcode references embedded in binary build targets
RUN go install github.com/SRI-CSL/gllvm/cmd/...@v1.3.1
ENV PATH=$PATH:/root/go/bin

# Clone llvm to build `libc++` from source
FROM base AS llvm-sources

RUN git clone --depth 1 --branch llvmorg-23.1.2 https://github.com/llvm/llvm-project.git /llvm-project

# TODO(msurovic): I don't think there is a reason why we should be building
# both `clean-libcxx` and `poly-libcxx`. The former is used when linking an
# uninstrumented target of the user project. The latter is used when linking
# the instrumented target of the user project. Not building either results in
# `libc++` symbols missing from the instrumented target. Why this happens is
# anyone's guess.

# Build "clean" `libc++` with `gclang`. Used to link the uninstrumented
# target of the user project. Installed into `/cxx_lib/clean_build`.
FROM llvm-sources AS clean-libcxx

ENV WLLVM_BC_STORE=/cxx_clean_bitcode
RUN mkdir -p $WLLVM_BC_STORE

ENV LIBCXX_BUILD_DIR=/llvm-project/build
ENV LIBCXX_INSTALL_DIR=/cxx_lib/clean_build

RUN cmake -GNinja \
  -B$LIBCXX_BUILD_DIR \
  -S/llvm-project/runtimes \
  -DCMAKE_BUILD_TYPE=${BUILD_TYPE} \
  -DCMAKE_C_COMPILER="gclang" \
  -DCMAKE_CXX_COMPILER="gclang++" \
  -DCMAKE_INSTALL_PREFIX=$LIBCXX_INSTALL_DIR \
  -DLIBCXXABI_ENABLE_SHARED=NO \
  -DLIBCXXABI_USE_LLVM_UNWINDER=OFF \
  -DLIBCXX_ENABLE_SHARED=NO \
  -DLLVM_ENABLE_PER_TARGET_RUNTIME_DIR=OFF \
  -DLLVM_INCLUDE_TESTS=OFF \
  -DLIBCXX_INCLUDE_TESTS=OFF \
  -DLIBCXXABI_INCLUDE_TESTS=OFF \
  -DLLVM_ENABLE_RUNTIMES="libcxx;libcxxabi"

RUN cmake --build $LIBCXX_BUILD_DIR --target install-cxx install-cxxabi -j${BUILD_JOBS}

# Build "poly" `libc++` with `gclang`. Used to link the instrumented
# target of the user project. Installed into `/cxx_lib/poly_build`.
FROM clean-libcxx AS poly-libcxx

ENV WLLVM_BC_STORE=/cxx_poly_bitcode
RUN mkdir -p $WLLVM_BC_STORE

ENV LIBCXX_BUILD_DIR=/llvm-project/llvm/build
ENV LIBCXX_INSTALL_DIR=/cxx_lib/poly_build

RUN cmake -GNinja \
  -B$LIBCXX_BUILD_DIR \
  -S/llvm-project/runtimes \
  -DCMAKE_BUILD_TYPE=${BUILD_TYPE} \
  -DCMAKE_C_COMPILER="gclang" \
  -DCMAKE_CXX_COMPILER="gclang++" \
  -DCMAKE_INSTALL_PREFIX=$LIBCXX_INSTALL_DIR \
  -DLIBCXXABI_ENABLE_SHARED=NO \
  -DLIBCXXABI_USE_LLVM_UNWINDER=OFF \
  -DLIBCXX_ENABLE_SHARED=NO \
  -DLLVM_ENABLE_PER_TARGET_RUNTIME_DIR=OFF \
  -DLIBCXX_ABI_VERSION=2 \
  -DLIBCXX_HERMETIC_STATIC_LIBRARY=ON \
  -DLIBCXX_ENABLE_STATIC_ABI_LIBRARY=ON \
  -DLLVM_INCLUDE_TESTS=OFF \
  -DLIBCXX_INCLUDE_TESTS=OFF \
  -DLIBCXXABI_INCLUDE_TESTS=OFF \
  -DLLVM_ENABLE_RUNTIMES="libcxx;libcxxabi"

RUN cmake --build $LIBCXX_BUILD_DIR --target install-cxx install-cxxabi -j${BUILD_JOBS}

# Build and install the polytracker
FROM poly-libcxx AS polytracker

ARG DFSAN_FILENAME_ARCH=x86_64

WORKDIR /workdir
COPY . /polytracker

RUN pip3 install /polytracker

RUN cmake -GNinja \
  -B/polytracker-build \
  -S/polytracker \
  -DCMAKE_BUILD_TYPE=${BUILD_TYPE} \
  -DCMAKE_C_COMPILER="clang" \
  -DCMAKE_CXX_COMPILER="clang++" \
  -DCXX_LIB_PATH=/cxx_lib/poly_build \
  -DCMAKE_INSTALL_PREFIX=/polytracker-install

RUN cmake --build /polytracker-build --target install -j${BUILD_JOBS}

ENV DFSAN_LIB_PATH=/polytracker-install/lib/linux/libclang_rt.dfsan-${DFSAN_FILENAME_ARCH}.a
ENV CXX_LIB_PATH=/cxx_lib
ENV COMPILER_DIR=/polytracker-install/share/polytracker

ENV DFSAN_OPTIONS="strict_data_dependencies=0"

ENV WLLVM_BC_STORE=/project_bitcode
ENV WLLVM_ARTIFACT_STORE=/project_artifacts

RUN mkdir $WLLVM_ARTIFACT_STORE && mkdir $WLLVM_BC_STORE

ENV PATH=$PATH:/polytracker-install/bin
