# Android Capture Standard

## v1
One canonical emulator profile selected for reliability and ad-friendly capture.

## Required capabilities
- boot/readiness
- APK install
- package launch
- clear/reset state
- fictional demo state
- navigation
- tap/swipe/type
- screenshot
- screenrecord
- pull files
- expected-screen validation

## Tool preference
ADB first; add Maestro/UIAutomator where they improve reliability.

## Safety
Never ingest or expose real production/private user data for advertising captures.

## Fallback
If emulator unavailable, generate the mandatory Emulator Capture Handoff Package and transition to WAITING_FOR_EXTERNAL_ASSET.
