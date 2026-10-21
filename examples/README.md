# Example workflows

Not in the default library: tools you only need now and then, alternatives and experiments. Most of them use the ComfyBridge nodes (Load EXR, Crop To Mask / Stitch), so they need a local ComfyUI; the Cloud column says which run on Comfy Cloud.
Copy one into `comfy_bridge_workflows` (next to `comfy_bridge.py`) and press **load / reload**.

| Workflow | Status | Cloud |
|---|---|---|
| Test – colour round-trip | tool: checks the colour round-trip (difference should be ~black) | yes |
| Test – extra input | tool: returns an extra input exactly as ComfyUI sees it | no |
| Render to photo + depth + edges | untested | no |
| Mix passes + inpaint – Qwen-Image 2512 | untested | no |
| Render to photo – style reference (Qwen-Image-Edit 2509) | works, but tends to paste objects from the reference instead of copying its style | no |
| Depth to photo – style reference (Qwen-Image-Edit 2509) | same | no |
| Edit – Qwen-Image-Edit 2509 (mask, SAM3 matte) | tested (crop / stitch nodes); on Cloud use Inpaint – Qwen-Image InstantX or Edit 2511 | no |
| Edit – Qwen Image 2.1 (plate + reference) | tested on Cloud; Edit 2511 keeps the plate better | yes |
| Render to photo – Qwen-Image 2512 + depth, FAST (Lightning) | tested | no |
| Render to photo – Qwen-Image 2512 + depth (3D, float EXR depth) | tested (Load EXR); best depth precision | no |
| Inpaint – SDXL (mask) | tested (crop / stitch nodes); light on VRAM (8 GB) | no |
| Realism – SDXL img2img + Tile | tested; light on VRAM | yes |
