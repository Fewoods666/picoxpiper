# Install script for directory: /home/wtc/realsense_src/librealsense/examples

# Set the install prefix
if(NOT DEFINED CMAKE_INSTALL_PREFIX)
  set(CMAKE_INSTALL_PREFIX "/home/wtc/realsense_src/sdk")
endif()
string(REGEX REPLACE "/$" "" CMAKE_INSTALL_PREFIX "${CMAKE_INSTALL_PREFIX}")

# Set the install configuration name.
if(NOT DEFINED CMAKE_INSTALL_CONFIG_NAME)
  if(BUILD_TYPE)
    string(REGEX REPLACE "^[^A-Za-z0-9_]+" ""
           CMAKE_INSTALL_CONFIG_NAME "${BUILD_TYPE}")
  else()
    set(CMAKE_INSTALL_CONFIG_NAME "Release")
  endif()
  message(STATUS "Install configuration: \"${CMAKE_INSTALL_CONFIG_NAME}\"")
endif()

# Set the component getting installed.
if(NOT CMAKE_INSTALL_COMPONENT)
  if(COMPONENT)
    message(STATUS "Install component: \"${COMPONENT}\"")
    set(CMAKE_INSTALL_COMPONENT "${COMPONENT}")
  else()
    set(CMAKE_INSTALL_COMPONENT)
  endif()
endif()

# Install shared libraries without execute permission?
if(NOT DEFINED CMAKE_INSTALL_SO_NO_EXE)
  set(CMAKE_INSTALL_SO_NO_EXE "1")
endif()

# Is this installation the result of a crosscompile?
if(NOT DEFINED CMAKE_CROSSCOMPILING)
  set(CMAKE_CROSSCOMPILING "FALSE")
endif()

# Set default install directory permissions.
if(NOT DEFINED CMAKE_OBJDUMP)
  set(CMAKE_OBJDUMP "/usr/bin/objdump")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for each subdirectory.
  include("/home/wtc/realsense_src/build/examples/hello-realsense/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/gpu-frame/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/software-device/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/capture/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/callback/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/save-to-disk/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/multicam/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/pointcloud/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/labeledpointcloud/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/align/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/align-gl/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/align-advanced/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/sensor-control/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/measure/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/C/depth/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/C/color/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/C/distance/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/C/infrared/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/post-processing/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/record-playback/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/motion/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/gl/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/hdr/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/on-chip-calib/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/eth-config/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/embedded-filters/cmake_install.cmake")
  include("/home/wtc/realsense_src/build/examples/object-detection/cmake_install.cmake")

endif()

