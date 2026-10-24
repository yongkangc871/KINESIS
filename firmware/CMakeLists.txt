# Wrist Curl Rehab Monitor - ESP32 acquisition firmware (ESP-IDF >= 5.1)
cmake_minimum_required(VERSION 3.16)
# The Wi-Fi password lives in sdkconfig.secrets (git-ignored, see sdkconfig.secrets.example),
# so it never ends up in the repository.
if(EXISTS "${CMAKE_CURRENT_LIST_DIR}/sdkconfig.secrets")
    set(SDKCONFIG_DEFAULTS "sdkconfig.defaults;sdkconfig.secrets")
endif()
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(wristmon_fw)
