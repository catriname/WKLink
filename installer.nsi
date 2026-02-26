; WKLink NSIS Installer Script
; Produces a Windows installer with Start Menu + desktop shortcuts.
; Run: makensis installer.nsi  (after PyInstaller has produced dist\WKLink.exe)

!define APP_NAME   "WKLink"
!define APP_EXE    "WKLink.exe"
!define SETUP_NAME "WKLink-Setup.exe"

Name "${APP_NAME}"
OutFile "dist\${SETUP_NAME}"
InstallDir "$PROGRAMFILES64\${APP_NAME}"
InstallDirRegKey HKLM "Software\${APP_NAME}" "InstallDir"
RequestExecutionLevel admin

SetCompressor /SOLID lzma
Unicode True

; Modern UI
!include "MUI2.nsh"
!define MUI_ABORTWARNING

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; ── Install ──────────────────────────────────────────────────────────────────

Section "Install"

  SetOutPath "$INSTDIR"
  File "dist\${APP_EXE}"

  ; Shortcuts
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut  "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
  CreateShortcut  "$DESKTOP\${APP_NAME}.lnk"                "$INSTDIR\${APP_EXE}"

  ; Uninstaller
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; Add / Remove Programs entry
  WriteRegStr  HKLM "Software\${APP_NAME}" "InstallDir" "$INSTDIR"
  WriteRegStr  HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
               "DisplayName"    "${APP_NAME} — WinKeyer to VBand Bridge"
  WriteRegStr  HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
               "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr  HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
               "InstallLocation" "$INSTDIR"
  WriteRegStr  HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
               "Publisher"      "K5GRR"
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
               "NoModify" 1
  WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
               "NoRepair"  1

SectionEnd

; ── Uninstall ────────────────────────────────────────────────────────────────

Section "Uninstall"

  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir  "$INSTDIR"

  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  RMDir  "$SMPROGRAMS\${APP_NAME}"
  Delete "$DESKTOP\${APP_NAME}.lnk"

  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
  DeleteRegKey HKLM "Software\${APP_NAME}"

SectionEnd
