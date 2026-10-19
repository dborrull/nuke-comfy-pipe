# Example workflows

Not in the default library: experimental, untested or tools you only need now and then.
Copy one into `comfy_bridge_workflows` (next to `comfy_bridge.py`) and press **load / reload**.

| Workflow | Status |
|---|---|
| Test – colour round-trip | tool: checks the colour round-trip (difference should be ~black) |
| Test – extra input | tool: returns an extra input exactly as ComfyUI sees it |
| Render to photo + depth + edges | untested |
| Mix passes + inpaint – Qwen-Image 2512 | untested |
| Render to photo – style reference (Qwen-Image-Edit 2509) | works, but tends to paste objects from the reference instead of copying its style |
| Depth to photo – style reference (Qwen-Image-Edit 2509) | same |
| Upscale – SeedVR2 3B Int8 | adapted from the official ComfyUI template, not tested yet |
| Edit – Qwen Image 2.1 (plate + reference) | adapted from the official ComfyUI template, not tested yet |
