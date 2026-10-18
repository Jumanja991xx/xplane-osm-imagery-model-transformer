# Standalone build of Laminar Research's DSFTool (MIT) from a pinned xptools
# checkout. Only zlib is required; it is fetched from source when it is not
# available on the system (always the case on Windows CI).
#
#   cmake -S tools/dsftool -B tools/dsftool/build -DXPTOOLS_DIR=<xptools checkout>
#   cmake --build tools/dsftool/build --config Release
#
# tools/build_dsftool.py does the checkout and both steps.

cmake_minimum_required(VERSION 3.16)
project(dsftool_standalone C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT XPTOOLS_DIR)
  message(FATAL_ERROR "Set -DXPTOOLS_DIR=<path to xptools checkout>")
endif()

option(XPSB_FETCH_ZLIB "Always build zlib from source" OFF)

if(NOT XPSB_FETCH_ZLIB)
  find_package(ZLIB QUIET)
endif()

if(NOT ZLIB_FOUND)
  include(FetchContent)
  FetchContent_Declare(zlib
    URL https://github.com/madler/zlib/releases/download/v1.3.1/zlib-1.3.1.tar.gz
    URL_HASH SHA256=9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23
  )
  set(ZLIB_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
  FetchContent_MakeAvailable(zlib)
  target_include_directories(zlibstatic INTERFACE ${zlib_SOURCE_DIR} ${zlib_BINARY_DIR})
  add_library(ZLIB::ZLIB ALIAS zlibstatic)
endif()

set(X ${XPTOOLS_DIR})

add_executable(DSFTool
  ${X}/src/DSF/DSFLib.cpp
  ${X}/src/DSF/DSFLib_Print.cpp
  ${X}/src/DSF/DSFLibWrite.cpp
  ${X}/src/DSF/DSFPointPool.cpp
  ${X}/src/DSFTools/DSFToolCmdLine.cpp
  ${X}/src/DSFTools/DSF2Text.cpp
  ${X}/src/Utils/AssertUtils.cpp
  ${X}/src/Utils/EndianUtils.c
  ${X}/src/Utils/FileUtils.cpp
  ${X}/src/Utils/MemFileUtils.cpp
  ${X}/src/GUI/GUI_Unicode.cpp
  ${X}/src/Utils/md5.c
  ${X}/src/Utils/zip.c
  ${X}/src/Utils/unzip.c
  ${X}/src/Utils/XChunkyFileUtils.cpp
  ${X}/src/DSF/tri_stripper_101/tri_stripper.cpp
  ${X}/src/lzma19/C/7zArcIn.c
  ${X}/src/lzma19/C/7zAlloc.c
  ${X}/src/lzma19/C/7zBuf.c
  ${X}/src/lzma19/C/7zCrc.c
  ${X}/src/lzma19/C/7zCrcOpt.c
  ${X}/src/lzma19/C/7zDec.c
  ${X}/src/lzma19/C/7zFile.c
  ${X}/src/lzma19/C/7zStream.c
  ${X}/src/lzma19/C/Bcj2.c
  ${X}/src/lzma19/C/Bra.c
  ${X}/src/lzma19/C/Bra86.c
  ${X}/src/lzma19/C/BraIA64.c
  ${X}/src/lzma19/C/CpuArch.c
  ${X}/src/lzma19/C/Delta.c
  ${X}/src/lzma19/C/LzmaDec.c
  ${X}/src/lzma19/C/Lzma2Dec.c
)

if(WIN32)
  set(PLATFORM_DEFINES LIN=0 IBM=1 APL=0)
elseif(APPLE)
  set(PLATFORM_DEFINES LIN=0 IBM=0 APL=1 _LIBCPP_ENABLE_CXX17_REMOVED_UNARY_BINARY_FUNCTION=1)
else()
  set(PLATFORM_DEFINES LIN=1 IBM=0 APL=0 _LIBCPP_ENABLE_CXX17_REMOVED_UNARY_BINARY_FUNCTION=1)
endif()

target_compile_definitions(DSFTool PRIVATE ${PLATFORM_DEFINES} DEV=0 BIG=0 LIL=1)

if(MSVC)
  target_compile_options(DSFTool PRIVATE /FI${X}/src/Obj/XDefs.h)
  # Static CRT: DSFTool.exe runs without a VC++ redistributable.
  set_property(TARGET DSFTool PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded")
  if(TARGET zlibstatic)
    set_property(TARGET zlibstatic PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded")
  endif()
else()
  target_compile_options(DSFTool PRIVATE -include ${X}/src/Obj/XDefs.h)
endif()

target_include_directories(DSFTool PRIVATE
  ${X}/src/GUI
  ${X}/src/Utils
  ${X}/src/DSF
  ${X}/src/DSF/tri_stripper_101
  ${X}/src/lzma19/C
  ${X}/src/Obj
)
target_link_libraries(DSFTool PRIVATE ZLIB::ZLIB)
