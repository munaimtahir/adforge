# Device Composition

`CompositionInstruction` supports `RAW_FULL_SCREEN` and `DEVICE_FRAME`, with scale, crop, screen mask, frame asset, background, shadow, and safe margin. Device-frame rendering is intentionally a presentation wrapper around genuine captured footage; it does not synthesize application UI. The next integration step is wiring these instructions into the renderer's filter builder.
