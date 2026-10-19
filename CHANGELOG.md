# Changelog

## 0.8.0
- **Comfy Cloud backend:** new *run on* selector (local ComfyUI / Comfy Cloud); the local address stays in *server* for when you switch back. Runs workflows on Comfy Cloud instead of a local ComfyUI (paid plan). The API key is read from `Documents/ComfyBridge/comfy_cloud_api_key.txt` or the `COMFY_API_KEY` variable, never from the Nuke script, and is not sent to the storage host when results are downloaded.
- On Cloud, *ComfyBridge Save EXR* is swapped for the core *Save Image (Advanced)* in EXR (16/32-bit float), so results still come back as float EXR. Workflows that need other ComfyBridge nodes (Load EXR, crop/stitch for masks) are flagged "not for Cloud" when loaded.
- Repo: SeedVR2 upscale and Qwen Image 2.1 edit moved to `examples/` until tested; README updated (Comfy Cloud section, workflow table with Cloud support).
- *test connection* on Cloud checks the key and shows the account; the models line says which nodes Cloud lacks; cancelling also removes the queued Cloud jobs.

## 0.7.2
- Every workflow with a KSampler now shows its sampler settings in the Parameters tab (seed, steps, cfg, sampler, scheduler, denoise), even if the metadata does not list them. Sampler and scheduler are dropdowns from your ComfyUI. Turn off per workflow with `"sampler_params": false`.

## 0.7.1
- Workflow: Edit – Qwen Image 2.1 (plate + reference in aux, optional prompt enhancer).
- Parameters can be on/off checkboxes and can drive several inputs at once (`"also"`).

## 0.7.0
- **Models made easy:** on load, the node lists the models the workflow needs and checks them against your ComfyUI (`models: all 4 found` / `2 missing`). **download missing models** fetches them into the right ComfyUI folder (asked once, remembered), with progress, cancel and resume. Licences are shown before downloading.
- `models_catalog.json` maps model files to their official download links; workflows can add their own with `"models": [...]`.
- Library trimmed to tested workflows; the rest moved to `examples/`.
- New workflow: Render to photo – depth image (PNG).

## 0.6.6
- Workflow: Upscale – SeedVR2 3B Int8 (ComfyUI native nodes).
- Workflow metadata can set `"match_format": false`, so upscalers keep their bigger size.

## 0.6.5
- Code split into a `comfybridge` package (client, workflows, templates, help, Nuke UI). `comfy_bridge.py` stays as the entry point, so existing nodes and `menu.py` keep working.
- Errors that used to be swallowed are now printed in the Script Editor as `[ComfyBridge] ...`.

## 0.6.4
- Every parameter gets a help text: from the workflow, or a default one for the usual inputs (denoise, cfg, steps, ControlNet strength, models...).

## 0.6.3
- Tooltips on every knob, a help button with a quick guide, parameter tooltips naming the ComfyUI node / input they drive.

## 0.6.2
- Input layers matched by name: a workflow asking for `depth` picks Blender's `ViewLayer_Depth` automatically.

## 0.6.1
- Model / LoRA / ControlNet parameters are dropdowns with the real list from ComfyUI (`/object_info`); files in another subfolder are found automatically.
- `send exposure`: brighten or darken what is sent, in stops; colour results come back with the inverse.
- Workflows: Render to photo FAST (Lightning), depth + edges, style reference, depth-to-photo style reference.

## 0.6
- Multi-input: up to 3 extra images per workflow (depth, a second pass, AOVs), from a layer of the plate, the new `aux` input or the mask input. Data goes as float EXR, read in ComfyUI by **Load EXR (ComfyBridge)**, which also normalises depth for depth ControlNets.
- Mask can be sent as alpha or RGBA (colour-coded masks).
- Workflows: Render to photo (Qwen-Image 2512 + depth), Mix passes + inpaint, Test – extra input.

## 0.5
- Masks: mask input or plate alpha, grow / soften, keymix comp with feather.
- ComfyUI nodes Crop To Mask / Stitch (optional colour match, matte to alpha) and Mark Mask.
- Workflows: Inpaint SDXL, Edit Qwen-Image-Edit 2509 with SAM3 matte.

## 0.4
- English UI. Works within Nuke Non-commercial's Python limit (node pasted from a template, internals driven by expressions).
- Result can come out of the node itself; standalone Read placed next to the node after each run.

## 0.3
- Workflow library with parameters, colour round-trip through the Viewer transform, versioned outputs, float EXR output node.
