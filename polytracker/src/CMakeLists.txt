find_package(LLVM CONFIG REQUIRED)
if(NOT LLVM_VERSION_MAJOR EQUAL 23)
  message(FATAL_ERROR "PolyTracker requires LLVM 23; found ${LLVM_PACKAGE_VERSION}")
endif()
set(LLVM_CONFIG_PATH "${LLVM_TOOLS_BINARY_DIR}/llvm-config" CACHE FILEPATH
    "Path to the llvm-config matching the pass toolchain")

add_definitions(${LLVM_DEFINITIONS})
include_directories(${LLVM_INCLUDE_DIRS})
add_library(LLVMPassConfig INTERFACE IMPORTED)
set_target_properties(
  LLVMPassConfig
  PROPERTIES
    INTERFACE_COMPILE_OPTIONS "-fno-rtti"
    INTERFACE_INCLUDE_DIRECTORIES "${LLVM_INCLUDE_DIRS}"
    INTERFACE_LINK_DIRECTORIES "${LLVM_LIBRARY_DIRS}"
    INTERFACE_COMPILE_DEFINITIONS
    "LLVM_VERSION_MAJOR=${LLVM_VERSION_MAJOR};LLVM_VERSION_MINOR=${LLVM_VERSION_MINOR}"
)

add_subdirectory(passes)

set(COMPILER_RT_SANITIZERS_TO_BUILD dfsan CACHE STRING "")
set(COMPILER_RT_BUILD_BUILTINS OFF CACHE BOOL "")
set(COMPILER_RT_BUILD_CRT OFF CACHE BOOL "")
set(COMPILER_RT_BUILD_PROFILE OFF CACHE BOOL "")
set(COMPILER_RT_BUILD_XRAY OFF CACHE BOOL "")
set(COMPILER_RT_BUILD_LIBFUZZER OFF CACHE BOOL "")
set(COMPILER_RT_BUILD_MEMPROF OFF CACHE BOOL "")

# The vendored compiler-rt still uses LLVM's former TARGET_TRIPLE name.
if(CMAKE_C_COMPILER_TARGET)
  set(TARGET_TRIPLE "${CMAKE_C_COMPILER_TARGET}")
else()
  set(TARGET_TRIPLE "${LLVM_TARGET_TRIPLE}")
endif()
add_subdirectory(compiler-rt)

set(POLYTRACKER_DIR polytracker)
set(POLYTRACKER_SOURCES ${POLYTRACKER_DIR}/main.cpp
                        ${POLYTRACKER_DIR}/polytracker.cpp)

set(TAINT_DIR taint_sources)
set(TAINT_SOURCES
    ${TAINT_DIR}/taint_sources.cpp ${TAINT_DIR}/string_taints.cpp
    ${TAINT_DIR}/memory_taints.cpp ${TAINT_DIR}/write_taints.cpp
    ${TAINT_DIR}/argv.cpp)

set(TAINTDAG_DIR taintdag)
set(TAINTDAG_SOURCES
    ${TAINTDAG_DIR}/encoding.cpp
    ${TAINTDAG_DIR}/error.cpp
    ${TAINTDAG_DIR}/polytracker.cpp
    ${TAINTDAG_DIR}/print.cpp
    ${TAINTDAG_DIR}/fnmapping.cpp
    ${TAINTDAG_DIR}/fntrace.cpp
    ${TAINTDAG_DIR}/util.cpp)

add_library(Polytracker STATIC ${POLYTRACKER_SOURCES} ${TAINT_SOURCES}
                               ${TAINTDAG_SOURCES})

option(POLYTRACKER_USE_LIBCXX "Build the PolyTracker runtime with libc++" ON)

set(POLYTRACKER_COMPILE_OPTS -fno-exceptions -fno-rtti -Wall -Werror)

if(POLYTRACKER_USE_LIBCXX)
  list(APPEND POLYTRACKER_COMPILE_OPTS -stdlib=libc++)
  target_include_directories(Polytracker PRIVATE ${CXX_LIB_PATH}/include/c++/v1)
  target_link_directories(Polytracker PUBLIC ${CXX_LIB_PATH}/lib)
  target_link_libraries(Polytracker c++)
endif()

if(XRAY_ON)
  list(APPEND POLYTRACKER_COMPILE_OPTS -fxray-instrument
       -fxray-instruction-threshold=1)
endif()

target_include_directories(Polytracker PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/compiler-rt/include)
target_compile_options(Polytracker PRIVATE ${POLYTRACKER_COMPILE_OPTS})
target_link_libraries(Polytracker LLVMPassConfig spdlog::spdlog_header_only)
set_property(TARGET Polytracker PROPERTY POSITION_INDEPENDENT_CODE ON)

install(TARGETS Polytracker DESTINATION ${POLYTRACKER_LIB_DIR})
