# Android Cinematography

`AndroidAction` supports typed WAIT, TAP, TAP_TEXT, TAP_COORDINATE, TYPE_TEXT, CLEAR_TEXT, SWIPE, BACK, HOME, keyboard controls, SCREENSHOT, assertions, package assertion, and HOLD. It contains no shell-command field. `AndroidActionExecutor` accepts only a narrow adapter protocol, validates capture bounds and filenames, and reports explicit failure codes. Existing `ADBAdapter` remains the production subprocess boundary.
