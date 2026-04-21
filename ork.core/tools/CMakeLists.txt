project(ork.python.wrapper)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Wall -Wextra -Wno-unknown-pragmas -Wno-unused-variable -Wno-unused-parameter -Wno-missing-field-initializers")
set(CMAKE_CXX_FLAGS_DEBUG "${CMAKE_CXX_FLAGS_DEBUG} -O0 -g")

add_executable(ork.python.wrapper ork_python_wrapper.cpp)
target_link_libraries(ork.python.wrapper PRIVATE c m )
set_target_properties(ork.python.wrapper PROPERTIES OUTPUT_NAME "ork.python")

IF(DEFINED SANITIZER AND NOT SANITIZER STREQUAL "")
  target_compile_definitions(ork.python.wrapper PRIVATE ORK_SANITIZER="${SANITIZER}")
ENDIF()

install(TARGETS ork.python.wrapper DESTINATION $ENV{OBT_SUBSPACE_BIN_DIR} )

###############################################################################
# obt-app-launcher (macOS only)
#
# Tiny Mach-O launcher used as CFBundleExecutable for orkid-based .app bundles
# produced by ork.deploy.macos.relocatable. See obt_app_launcher.c for the
# rationale (script-bundle TCC identity problem). One byte-identical binary
# is dropped into every .app bundle across every orkid-based project.
###############################################################################
if(APPLE)
  add_executable(obt_app_launcher obt_app_launcher.c)
  set_target_properties(obt_app_launcher PROPERTIES
    OUTPUT_NAME "obt-app-launcher"
    OSX_ARCHITECTURES "arm64;x86_64"
    C_STANDARD 11
  )
  target_compile_options(obt_app_launcher PRIVATE -Os -Wall -Wextra)
  install(TARGETS obt_app_launcher DESTINATION $ENV{OBT_SUBSPACE_BIN_DIR})

  ###############################################################################
  # request_permissions (macOS only)
  #
  # Tiny ObjC helper dropped into every gui-mode .app bundle (alongside
  # obt-app-launcher). Called once during downloader install — or on first
  # launch as a fallback — to request Camera and Microphone TCC grants while
  # the Mac display is visible, BEFORE any VR/HMD takeover in the real app
  # would hide a later prompt. TCC attribution is per-bundle-id, so this
  # binary must be re-signed with each target app's CFBundleIdentifier at
  # install time — see ork.deploy._install_macho_launcher.
  ###############################################################################
  add_executable(request_permissions request_permissions.m)
  set_target_properties(request_permissions PROPERTIES
    OUTPUT_NAME "request-permissions"
    OSX_ARCHITECTURES "arm64;x86_64"
  )
  target_compile_options(request_permissions PRIVATE -Os -Wall -Wextra -fobjc-arc)
  target_link_libraries(request_permissions PRIVATE
    "-framework AVFoundation"
    "-framework Foundation"
  )
  install(TARGETS request_permissions DESTINATION $ENV{OBT_SUBSPACE_BIN_DIR})
endif()
