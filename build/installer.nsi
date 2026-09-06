; ===========================================================================
;  TurboFetch Download Manager - NSIS installer
;
;  Wraps the portable build/dist/TurboFetch.exe into a
;  classic setup package with Start Menu / Desktop shortcuts and an
;  uninstaller.
;
;  Build (from the repository root):
;      makensis build\installer.nsi
;
;  Keep APP_VERSION in sync with utils/constants.py (APP_VERSION).
; ===========================================================================

!define APP_NAME      "TurboFetch Download Manager"
!define APP_SHORT     "TurboFetch"
!define APP_VERSION   "1.0.0"
!define APP_PUBLISHER "sufyan-vip"
!define APP_EXE       "TurboFetch.exe"

; NOTE: do not !cd anywhere – makensis already switches to the directory of
; this script by default (unless /NOCD is given), and all paths below are
; relative to it (dist\..., app_icon.ico).

Name "${APP_NAME} ${APP_VERSION}"
OutFile "dist\TurboFetch-Setup-${APP_VERSION}.exe"
InstallDir "$PROGRAMFILES64\${APP_SHORT}"
InstallDirRegKey HKLM "Software\${APP_SHORT}" "InstallDir"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
Unicode true

; ---------------------------------------------------------------- pages
!include "MUI2.nsh"

!define MUI_ABORTWARNING
!define MUI_ICON "app_icon.ico"
!define MUI_UNICON "app_icon.ico"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Start ${APP_NAME}"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "English"

; ---------------------------------------------------------------- sections
Section "Install" SecInstall
    SectionIn RO
    SetOutPath "$INSTDIR"
    File "dist\${APP_EXE}"

    WriteRegStr HKLM "Software\${APP_SHORT}" "InstallDir" "$INSTDIR"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_SHORT}" \
                  "DisplayName"     "${APP_NAME}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_SHORT}" \
                  "DisplayVersion"  "${APP_VERSION}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_SHORT}" \
                  "Publisher"       "${APP_PUBLISHER}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_SHORT}" \
                  "DisplayIcon"     "$INSTDIR\${APP_EXE}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_SHORT}" \
                  "UninstallString" '"$INSTDIR\Uninstall ${APP_SHORT}.exe"'
    WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_SHORT}" \
                  "NoModify" 1
    WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_SHORT}" \
                  "NoRepair" 1

    WriteUninstaller "$INSTDIR\Uninstall ${APP_SHORT}.exe"

    CreateDirectory "$SMPROGRAMS\${APP_SHORT}"
    CreateShortcut  "$SMPROGRAMS\${APP_SHORT}\${APP_NAME}.lnk"    "$INSTDIR\${APP_EXE}"
    CreateShortcut  "$SMPROGRAMS\${APP_SHORT}\Uninstall ${APP_SHORT}.lnk" "$INSTDIR\Uninstall ${APP_SHORT}.exe"
    CreateShortcut  "$DESKTOP\${APP_NAME}.lnk"                    "$INSTDIR\${APP_EXE}"
    CreateShortcut  "$SENDTO\${APP_NAME}.lnk"                     "$INSTDIR\${APP_EXE}"
SectionEnd

Section "Uninstall"
    Delete "$SMPROGRAMS\${APP_SHORT}\${APP_NAME}.lnk"
    Delete "$SMPROGRAMS\${APP_SHORT}\Uninstall ${APP_SHORT}.lnk"
    Delete "$DESKTOP\${APP_NAME}.lnk"
    Delete "$SENDTO\${APP_NAME}.lnk"
    RMDir  "$SMPROGRAMS\${APP_SHORT}"

    DeleteRegKey  HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_SHORT}"
    DeleteRegKey  HKLM "Software\${APP_SHORT}"

    Delete "$INSTDIR\${APP_EXE}"
    Delete "$INSTDIR\Uninstall ${APP_SHORT}.exe"
    RMDir  "$INSTDIR"
SectionEnd
